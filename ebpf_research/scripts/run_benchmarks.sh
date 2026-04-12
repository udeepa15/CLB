#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BPF_DIR="${ROOT_DIR}/bpf"
BUNDLES_DIR="${ROOT_DIR}/bundles"
RESULTS_DIR="${ROOT_DIR}/results"
METRICS_CSV="${RESULTS_DIR}/metrics.csv"
RAW_DIR="${RESULTS_DIR}/raw"

DURATION_SECONDS=60
TARGET_URL="http://10.200.0.2:8080/"
ATTACKER_TARGET_RPS=1000
FORTIO_PERCENTILES="50,95,99"
VICTIM_IF="veth_vic_h"
ATTACKER_IF="veth_att_h"
VICTIM_NS="victim_ns"
ATTACKER_NS="attacker_ns"

require_root() {
    if [[ "$(id -u)" -ne 0 ]]; then
        echo "[run] Run as root: sudo ./scripts/run_benchmarks.sh"
        exit 1
    fi
}

require_bin() {
    local missing=0
    for bin in runc ip tc bpftool jq fortio wrk2; do
        if ! command -v "${bin}" >/dev/null 2>&1; then
            echo "[run] Missing dependency: ${bin}"
            missing=1
        fi
    done
    if [[ "${missing}" -ne 0 ]]; then
        exit 1
    fi
}

ensure_setup() {
    if ! ip link show "${VICTIM_IF}" >/dev/null 2>&1; then
        echo "[run] Network not prepared. Running setup.sh first."
        "${SCRIPT_DIR}/setup.sh"
    fi
}

start_containers() {
    stop_containers
    runc run -d --bundle "${BUNDLES_DIR}/victim" victim_ct
    runc run -d --bundle "${BUNDLES_DIR}/attacker" attacker_ct
    wait_for_victim_ready
}

wait_for_victim_ready() {
    local tries=0
    while [[ "${tries}" -lt 30 ]]; do
        if timeout 1 bash -c 'cat < /dev/null > /dev/tcp/10.200.0.2/8080' >/dev/null 2>&1; then
            return 0
        fi
        tries=$((tries + 1))
        sleep 1
    done

    echo "[run] Victim endpoint did not become ready on 10.200.0.2:8080"
    runc state victim_ct || true
    return 1
}

stop_containers() {
    runc delete -f victim_ct >/dev/null 2>&1 || true
    runc delete -f attacker_ct >/dev/null 2>&1 || true
}

reset_tc() {
    tc filter del dev "${VICTIM_IF}" ingress >/dev/null 2>&1 || true
    tc filter del dev "${ATTACKER_IF}" ingress >/dev/null 2>&1 || true
    tc qdisc replace dev "${VICTIM_IF}" clsact
    tc qdisc replace dev "${ATTACKER_IF}" clsact
    rm -f /sys/fs/bpf/ebpf_research/tc_shared_prog >/dev/null 2>&1 || true
    rm -rf /sys/fs/bpf/ebpf_research/shared >/dev/null 2>&1 || true
}

apply_config_baseline() {
    reset_tc
}

apply_config_isolated() {
    reset_tc
    tc filter replace dev "${VICTIM_IF}" ingress bpf da obj "${BPF_DIR}/counter_tc_isolated.o" sec tc
    tc filter replace dev "${ATTACKER_IF}" ingress bpf da obj "${BPF_DIR}/counter_tc_isolated.o" sec tc
}

apply_config_shared() {
    reset_tc
    mkdir -p /sys/fs/bpf/ebpf_research
    bpftool prog loadall "${BPF_DIR}/counter_tc_shared.o" /sys/fs/bpf/ebpf_research/shared
    tc filter replace dev "${VICTIM_IF}" ingress bpf da pinned /sys/fs/bpf/ebpf_research/shared/count_ingress
    tc filter replace dev "${ATTACKER_IF}" ingress bpf da pinned /sys/fs/bpf/ebpf_research/shared/count_ingress
}

to_ms() {
    local value="$1"
    if [[ -z "${value}" || "${value}" == "null" || "${value}" == "NA" ]]; then
        echo "NA"
    else
        awk -v v="${value}" 'BEGIN { printf "%.3f", v * 1000.0 }'
    fi
}

extract_fortio_metric() {
    local json_file="$1"
    local percentile="$2"

    [[ -s "${json_file}" ]] || return 0

    jq -r --arg p "${percentile}" '
        if .DurationHistogram?.Percentiles then
            (.DurationHistogram.Percentiles[] | select((.Percentile|tostring) == $p) | .Value)
        elif .Percentiles then
            (.Percentiles[] | select((.Percentile|tostring) == $p) | .Value)
        else
            empty
        end
    ' "${json_file}" | tail -n 1
}

extract_fortio_qps() {
    local json_file="$1"
    [[ -s "${json_file}" ]] || return 0
    jq -r '(.ActualQPS // .actualQPS // .Labels?.ActualQPS // empty)' "${json_file}" | tail -n 1
}

extract_wrk2_rps() {
    local log_file="$1"
    awk '/Requests\/sec:/ {print $2}' "${log_file}" | tail -n 1
}

run_fortio_with_retry() {
    local json_file="$1"
    local log_file="$2"
    local tries=0

    while [[ "${tries}" -lt 2 ]]; do
        fortio load -t ${DURATION_SECONDS}s -qps 0 -c 16 -p "${FORTIO_PERCENTILES}" -json "${json_file}" "${TARGET_URL}" > "${log_file}" 2>&1 || true

        if [[ -s "${json_file}" ]]; then
            return 0
        fi

        tries=$((tries + 1))
        sleep 2
    done

    return 1
}

init_csv() {
    mkdir -p "${RAW_DIR}"
    if [[ ! -f "${METRICS_CSV}" ]]; then
        echo "timestamp,config,p50_ms,p95_ms,p99_ms,throughput_qps,attacker_rps" > "${METRICS_CSV}"
    fi
}

run_one() {
    local config_name="$1"
    local config_fn="$2"
    local ts
    ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

    echo "[run] Starting ${config_name}"
    "${config_fn}"

    local fortio_json="${RAW_DIR}/${config_name}_fortio.json"
    local fortio_log="${RAW_DIR}/${config_name}_fortio.log"
    local wrk2_log="${RAW_DIR}/${config_name}_wrk2.log"

    : > "${wrk2_log}"

    if [[ "${config_name}" != "baseline" ]]; then
        ip netns exec "${ATTACKER_NS}" wrk2 -t2 -c64 -d${DURATION_SECONDS}s -R${ATTACKER_TARGET_RPS} "${TARGET_URL}" > "${wrk2_log}" 2>&1 &
        local noise_pid=$!
    else
        local noise_pid=""
    fi

    run_fortio_with_retry "${fortio_json}" "${fortio_log}" || true

    if [[ -n "${noise_pid}" ]]; then
        wait "${noise_pid}" || true
    fi

    local p50 p95 p99 qps attacker_rps
    p50="$(extract_fortio_metric "${fortio_json}" "50")"
    p95="$(extract_fortio_metric "${fortio_json}" "95")"
    p99="$(extract_fortio_metric "${fortio_json}" "99")"
    qps="$(extract_fortio_qps "${fortio_json}")"
    attacker_rps="$(extract_wrk2_rps "${wrk2_log}")"

    [[ -z "${p50}" ]] && p50="NA"
    [[ -z "${p95}" ]] && p95="NA"
    [[ -z "${p99}" ]] && p99="NA"
    [[ -z "${qps}" ]] && qps="NA"
    [[ -z "${attacker_rps}" ]] && attacker_rps="0"

    echo "${ts},${config_name},$(to_ms "${p50}"),$(to_ms "${p95}"),$(to_ms "${p99}"),${qps},${attacker_rps}" >> "${METRICS_CSV}"
}

main() {
    require_root
    require_bin
    ensure_setup
    init_csv

    start_containers

    run_one "baseline" apply_config_baseline
    run_one "sidecar_isolation" apply_config_isolated
    run_one "sidecarless_contention" apply_config_shared

    reset_tc
    stop_containers

    echo "[run] Completed benchmark matrix."
    echo "[run] Metrics file: ${METRICS_CSV}"
}

main "$@"
