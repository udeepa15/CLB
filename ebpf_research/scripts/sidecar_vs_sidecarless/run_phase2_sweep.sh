#!/usr/bin/env bash
set -euo pipefail

# PHASE 2: High-Density Thundering Herd Test (5 Victims vs 1 Attacker)
# Sidecar vs sidecarless benchmark sweep across increasing attacker load.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BPF_DIR="${ROOT_DIR}/bpf"
BUNDLES_DIR="${ROOT_DIR}/bundles"
RESULTS_DIR="${ROOT_DIR}/results"
RAW_DIR="${RESULTS_DIR}/raw/phase2_5v1"
METRICS_CSV="${RESULTS_DIR}/phase2_5v1_metrics.csv"

# Tunables (can be overridden with env vars)
NUM_VICTIMS="${NUM_VICTIMS:-5}"
DURATION_SECONDS="${DURATION_SECONDS:-60}"
TARGET_URL="${TARGET_URL:-http://10.200.0.2:8080/}"
FORTIO_PERCENTILES="${FORTIO_PERCENTILES:-50,95,99}"
REPEATS="${REPEATS:-3}"
NOISE_LEVELS_CSV="${NOISE_LEVELS_CSV:-0,10000,20000,40000,60000}"
ATTACKER_LOAD_TOOL="${ATTACKER_LOAD_TOOL:-fortio}"

ATTACKER_IF="veth_att_h"
ATTACKER_NS="attacker_ns"
ATTACKER_PID=""
FORTIO_SINK_PID=""

require_root() {
    if [[ "$(id -u)" -ne 0 ]]; then
        echo "[run] Run as root: sudo ./run_phase2_sweep.sh"
        exit 1
    fi
}

require_bin() {
    local missing=0
    for bin in runc ip tc bpftool jq fortio awk mpstat; do
        if ! command -v "${bin}" >/dev/null 2>&1; then
            echo "[run] Missing dependency: ${bin} (You may need to run: sudo apt install sysstat)"
            missing=1
        fi
    done
    if [[ "${missing}" -ne 0 ]]; then
        exit 1
    fi
}

ensure_setup() {
    if ! ip link show "veth_vic1_h" >/dev/null 2>&1; then
        echo "[run] Network not prepared. You must run the updated multi-tenant setup.sh first."
        exit 1
    fi
}

start_containers() {
    stop_containers
    
    # Kill any zombie fortio servers from previous failed runs!
    killall fortio 2>/dev/null || true
    
    # Spin up all 5 Victim containers
    for i in $(seq 1 "${NUM_VICTIMS}"); do
        runc run -d --bundle "${BUNDLES_DIR}/victim${i}" "victim_ct${i}"
    done
    
    runc run -d --bundle "${BUNDLES_DIR}/attacker" attacker_ct
    wait_for_victim_ready

    # BIG FIX: Disable GRPC ports to prevent collisions and log the output so we can see if it fails
    echo "[run] Injecting high-speed sink into Victim 2 namespace..."
    ip netns exec victim_ns2 fortio server -http-port 8081 -grpc-port 0 -redirect-port 0 > "${RAW_DIR}/sink_server.log" 2>&1 &
    FORTIO_SINK_PID=$!
    
    # Give the sink 2 full seconds to bind to the port
    sleep 2 
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

    echo "[run] Victim 1 endpoint did not become ready on 10.200.0.2:8080"
    runc state victim_ct1 || true
    return 1
}

stop_containers() {
    # Kill the injected sink server
    if [[ -n "${FORTIO_SINK_PID}" ]]; then
        kill -9 "${FORTIO_SINK_PID}" 2>/dev/null || true
    fi

    # Kill all 5 Victim containers
    for i in $(seq 1 "${NUM_VICTIMS}"); do
        runc delete -f "victim_ct${i}" >/dev/null 2>&1 || true
    done
    runc delete -f attacker_ct >/dev/null 2>&1 || true
}

reset_tc() {
    # Clear tc hooks for all 5 victims
    for i in $(seq 1 "${NUM_VICTIMS}"); do
        tc filter del dev "veth_vic${i}_h" ingress >/dev/null 2>&1 || true
        tc qdisc replace dev "veth_vic${i}_h" clsact
    done
    
    tc filter del dev "${ATTACKER_IF}" ingress >/dev/null 2>&1 || true
    tc qdisc replace dev "${ATTACKER_IF}" clsact
    rm -f /sys/fs/bpf/ebpf_research/tc_shared_prog >/dev/null 2>&1 || true
    rm -rf /sys/fs/bpf/ebpf_research/shared >/dev/null 2>&1 || true
}

apply_config_isolated() {
    reset_tc
    # Attach isolated sidecar hooks to all 5 victims independently
    for i in $(seq 1 "${NUM_VICTIMS}"); do
        tc filter replace dev "veth_vic${i}_h" ingress bpf da obj "${BPF_DIR}/counter_tc_isolated.o" sec tc
    done
    
    tc filter replace dev "${ATTACKER_IF}" ingress bpf da obj "${BPF_DIR}/counter_tc_isolated.o" sec tc
}

apply_config_shared() {
    reset_tc
    mkdir -p /sys/fs/bpf/ebpf_research
    bpftool prog loadall "${BPF_DIR}/counter_tc_shared.o" /sys/fs/bpf/ebpf_research/shared
    
    # Force ALL 5 victims to share the exact same eBPF pinned map
    for i in $(seq 1 "${NUM_VICTIMS}"); do
        tc filter replace dev "veth_vic${i}_h" ingress bpf da pinned /sys/fs/bpf/ebpf_research/shared/count_ingress
    done
    
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

extract_fortio_text_rps() {
    local log_file="$1"
    # BIG FIX: Fallback to the new fortio format: "All done... 9999.6 qps"
    awk '
        /All done .* qps/ {
            match($0, /([0-9]+(\.[0-9]+)?)\s*qps/, m)
            v=m[1]
        }
        END {if (v != "") print v}
    ' "${log_file}" | tail -n 1
}

extract_attacker_rps() {
    local log_file="$1"
    if grep -q "^0$" "${log_file}"; then
        echo "0"
        return 0
    fi
    local rps
    rps="$(extract_wrk2_rps "${log_file}")"
    if [[ -z "${rps}" ]]; then
        rps="$(extract_fortio_text_rps "${log_file}")"
    fi
    echo "${rps}"
}

start_attacker_noise() {
    local noise_target_rps="$1"
    local attacker_log="$2"
    
    # Target the /echo endpoint for maximum throughput
    local ATTACK_URL="http://10.200.0.3:8081/echo"

    if [[ "${noise_target_rps}" -eq 0 ]]; then
        echo "0" > "${attacker_log}"
        ATTACKER_PID=""
        return 0
    fi

    (
        set +e
        if [[ "${ATTACKER_LOAD_TOOL}" == "wrk2" ]] && command -v wrk2 >/dev/null 2>&1; then
            ip netns exec "${ATTACKER_NS}" wrk2 -t2 -c64 -d"${DURATION_SECONDS}s" -R"${noise_target_rps}" "${ATTACK_URL}" >> "${attacker_log}" 2>&1
            rc=$?
            if [[ "${rc}" -eq 0 ]]; then
                exit 0
            fi
            echo "[run] wrk2 failed with rc=${rc}; falling back to fortio attacker load" >> "${attacker_log}"
        fi

        # We also boost fortio's concurrency to -c 64 here to ensure it can push 60k RPS
        ip netns exec "${ATTACKER_NS}" fortio load -t "${DURATION_SECONDS}s" -qps "${noise_target_rps}" -c 64 "${ATTACK_URL}" >> "${attacker_log}" 2>&1
        exit 0
    ) &
    ATTACKER_PID=$!
}

run_fortio_with_retry() {
    local json_file="$1"
    local log_file="$2"
    local tries=0

    while [[ "${tries}" -lt 2 ]]; do
        fortio load -a -t "${DURATION_SECONDS}s" -qps 1000 -c 4 -p "${FORTIO_PERCENTILES}" -json "${json_file}" "${TARGET_URL}" > "${log_file}" 2>&1 || true
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
        echo "timestamp,config,noise_target_rps,run_id,p50_ms,p95_ms,p99_ms,throughput_qps,attacker_rps" > "${METRICS_CSV}"
    fi
}

run_one() {
    local config_name="$1"
    local config_fn="$2"
    local noise_target_rps="$3"
    local run_id="$4"
    local ts
    ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

    echo "[run] config=${config_name}, noise_target_rps=${noise_target_rps}, run_id=${run_id}"
    "${config_fn}"

    local prefix="${config_name}_r${run_id}_n${noise_target_rps}"
    local fortio_json="${RAW_DIR}/${prefix}_fortio.json"
    local fortio_log="${RAW_DIR}/${prefix}_fortio.log"
    local attacker_log="${RAW_DIR}/${prefix}_attacker.log"
    local cpu_log="${RAW_DIR}/${prefix}_cpu_softirq.log"
    : > "${attacker_log}"

    mpstat -P ALL 1 $((DURATION_SECONDS + 5)) > "${cpu_log}" &
    local mpstat_pid=$!

    start_attacker_noise "${noise_target_rps}" "${attacker_log}"
    local noise_pid="${ATTACKER_PID}"

    echo "[run] Warming up 5-tenant network and eBPF maps for 3 seconds..."
    sleep 3

    run_fortio_with_retry "${fortio_json}" "${fortio_log}" || true
    
    if [[ -n "${noise_pid}" ]]; then
        wait "${noise_pid}" || true
    fi
    
    kill "${mpstat_pid}" 2>/dev/null || true
    wait "${mpstat_pid}" 2>/dev/null || true

    local p50 p95 p99 qps attacker_rps
    p50="$(extract_fortio_metric "${fortio_json}" "50")"
    p95="$(extract_fortio_metric "${fortio_json}" "95")"
    p99="$(extract_fortio_metric "${fortio_json}" "99")"
    qps="$(extract_fortio_qps "${fortio_json}")"
    attacker_rps="$(extract_attacker_rps "${attacker_log}")"

    [[ -z "${p50}" ]] && p50="NA"
    [[ -z "${p95}" ]] && p95="NA"
    [[ -z "${p99}" ]] && p99="NA"
    [[ -z "${qps}" ]] && qps="NA"
    [[ -z "${attacker_rps}" ]] && attacker_rps="NA"

    echo "${ts},${config_name},${noise_target_rps},${run_id},$(to_ms "${p50}"),$(to_ms "${p95}"),$(to_ms "${p99}"),${qps},${attacker_rps}" >> "${METRICS_CSV}"
}

main() {
    require_root
    require_bin
    ensure_setup
    init_csv

    local -a noise_levels
    IFS=',' read -r -a noise_levels <<< "${NOISE_LEVELS_CSV}"

    for run_id in $(seq 1 "${REPEATS}"); do
        for noise_target_rps in "${noise_levels[@]}"; do
            start_containers
            run_one "sidecar_isolation" apply_config_isolated "${noise_target_rps}" "${run_id}"
            run_one "sidecarless_contention" apply_config_shared "${noise_target_rps}" "${run_id}"
            reset_tc
            stop_containers
        done
    done

    echo "[run] Completed Phase 2 (5v1) sweep."
    echo "[run] Metrics file: ${METRICS_CSV}"
}

main "$@"