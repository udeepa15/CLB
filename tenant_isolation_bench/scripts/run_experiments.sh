#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BPF_DIR="${ROOT_DIR}/bpf"
RESULT_DIR="${ROOT_DIR}/results"
RAW_DIR="${RESULT_DIR}/raw"
DEPLOY_SCRIPT="${SCRIPT_DIR}/deploy_workloads.sh"
SUMMARY_SCRIPT="${SCRIPT_DIR}/clean_and_summarize.py"

MODES=(baseline isolated shared)
ACTIVE_COUNTS=(1 3 5)
ATTACKER_RATES=(0 10000 20000 25000 27500 30000 32500 35000 37500 40000 42500 45000)
VICTIM_QPS="${VICTIM_QPS:-200}"
VICTIM_THREADS="${VICTIM_THREADS:-2}"
VICTIM_CONNECTIONS="${VICTIM_CONNECTIONS:-100}"
WORKLOAD_DURATION="${WORKLOAD_DURATION:-60s}"
RUN_SECONDS="${RUN_SECONDS:-${WORKLOAD_DURATION%s}}"
WRK_BIN="${WRK_BIN:-wrk2}"
CASE_NUM=0
TOTAL_CASES=0
STOP_REQUESTED=0
CURRENT_VICTIM_PIDS=()
CURRENT_ATTACKER_PID=""

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    echo "[run] Run as root: sudo ./scripts/run_experiments.sh"
    exit 1
  fi
}

require_bin() {
  local missing=0
  for bin in ip tc bpftool clang make jq python3 awk; do
    if ! command -v "${bin}" >/dev/null 2>&1; then
      echo "[run] Missing dependency: ${bin}"
      missing=1
    fi
  done
  if ! command -v "${WRK_BIN}" >/dev/null 2>&1; then
    echo "[run] Missing dependency: ${WRK_BIN}"
    missing=1
  fi
  if command -v perf >/dev/null 2>&1; then
    PERF_AVAILABLE=1
    if [[ ! -e /sys/kernel/tracing/events/lock/lock_acquire ]] && ! perf list 2>/dev/null | grep -q 'lock:lock_acquire'; then
      PERF_AVAILABLE=0
      echo "[run] perf lock tracepoints unavailable; perf capture will be skipped"
    fi
  else
    PERF_AVAILABLE=0
    echo "[run] perf not available; perf capture will be skipped"
  fi
  if command -v bpftrace >/dev/null 2>&1; then
    BPFTRACE_AVAILABLE=1
  else
    BPFTRACE_AVAILABLE=0
    echo "[run] bpftrace not available; bpftrace capture will be skipped"
  fi
  if [[ "${missing}" -ne 0 ]]; then
    exit 1
  fi
}

show_progress() {
  # Prints a stable, single-line progress status that will not wrap.
  local label="${1:-}"
  if [[ ${TOTAL_CASES} -le 0 ]]; then
    return 0
  fi
  local percent=$(( CASE_NUM * 100 / TOTAL_CASES ))
  local width=24
  local filled=$(( percent * width / 100 ))
  local empty=$(( width - filled ))
  local filled_bar
  local empty_bar
  filled_bar=$(printf '%*s' "${filled}" '' | tr ' ' '#')
  empty_bar=$(printf '%*s' "${empty}" '' | tr ' ' '-')
  printf '\r\033[2K[progress] [%s%s] %5.1f%% %d/%d %s' \
    "${filled_bar}" "${empty_bar}" "${percent}" "${CASE_NUM}" "${TOTAL_CASES}" "${label}" >&2
}

show_progress_case() {
  local mode="$1"
  local active_count="$2"
  local attacker_rate="$3"
  local elapsed="$4"
  local total_seconds="$5"
  local completed_cases=$(( CASE_NUM - 1 ))
  local total_ticks=$(( TOTAL_CASES * total_seconds ))
  local completed_ticks=$(( completed_cases * total_seconds + elapsed ))
  local tenths=$(( completed_ticks * 1000 / total_ticks ))
  local whole=$(( tenths / 10 ))
  local frac=$(( tenths % 10 ))
  local rem=$(( total_seconds - elapsed ))
  printf '\r\033[2K[progress] case %d/%d | %s v%s r%s | elapsed %ss/%ss | remaining %ss | overall %d.%d%%' \
    "${CASE_NUM}" "${TOTAL_CASES}" "${mode}" "${active_count}" "${attacker_rate}" "${elapsed}" "${total_seconds}" "${rem}" "${whole}" "${frac}" >&2
}

victim_host_if() {
  echo "veth_v$1"
}

victim_ns() {
  echo "v_netns_$1"
}

victim_ip() {
  echo "10.200.$1.2"
}

victim_host_ip() {
  echo "10.200.$1.1/24"
}

adv_host_if() {
  echo "veth_adv"
}

adv_ns_ip() {
  echo "10.200.7.2"
}

adv_host_ip() {
  echo "10.200.7.1/24"
}

reset_tc() {
  for idx in 1 2 3 4 5 6; do
    tc qdisc del dev "$(victim_host_if "${idx}")" clsact >/dev/null 2>&1 || true
  done
  tc qdisc del dev "$(adv_host_if)" clsact >/dev/null 2>&1 || true
}

attach_isolated() {
  local active_count="$1"
  local idx
  for idx in $(seq 1 "${active_count}"); do
    tc qdisc replace dev "$(victim_host_if "${idx}")" clsact
    tc filter replace dev "$(victim_host_if "${idx}")" ingress bpf da obj "${BPF_DIR}/counter_tc_isolated.o" sec classifier
  done
  tc qdisc replace dev "$(adv_host_if)" clsact
  tc filter replace dev "$(adv_host_if)" ingress bpf da obj "${BPF_DIR}/counter_tc_isolated.o" sec classifier
}

shared_prog_dir="/sys/fs/bpf/tenant_isolation_bench/shared"

resolve_shared_prog_pin() {
  local candidate
  for candidate in "${shared_prog_dir}/count_ingress" "${shared_prog_dir}/classifier" "${shared_prog_dir}/handle_ingress"; do
    if [[ -e "${candidate}" ]]; then
      echo "${candidate}"
      return 0
    fi
  done
  candidate="$(find "${shared_prog_dir}" -maxdepth 1 -type f 2>/dev/null | head -n 1 || true)"
  if [[ -n "${candidate}" ]]; then
    echo "${candidate}"
    return 0
  fi
  echo "[run] Could not find pinned shared program" >&2
  return 1
}

prepare_shared() {
  rm -f /sys/fs/bpf/shared_counter_map >/dev/null 2>&1 || true
  rm -rf "${shared_prog_dir}" >/dev/null 2>&1 || true
  mkdir -p "$(dirname "${shared_prog_dir}")"
  bpftool prog loadall "${BPF_DIR}/counter_tc_shared.o" "${shared_prog_dir}"
}

attach_shared() {
  local active_count="$1"
  local shared_prog_pin
  local idx
  shared_prog_pin="$(resolve_shared_prog_pin)"
  for idx in $(seq 1 "${active_count}"); do
    tc qdisc replace dev "$(victim_host_if "${idx}")" clsact
    tc filter replace dev "$(victim_host_if "${idx}")" ingress bpf da pinned "${shared_prog_pin}"
  done
  tc qdisc replace dev "$(adv_host_if)" clsact
  tc filter replace dev "$(adv_host_if)" ingress bpf da pinned "${shared_prog_pin}"
}

cleanup_tracers() {
  if [[ -n "${BPFTRACE_PID:-}" ]]; then
    kill -INT "${BPFTRACE_PID}" >/dev/null 2>&1 || true
    wait "${BPFTRACE_PID}" >/dev/null 2>&1 || true
    BPFTRACE_PID=""
  fi
  if [[ -n "${PERF_PID:-}" ]]; then
    kill -INT "${PERF_PID}" >/dev/null 2>&1 || true
    wait "${PERF_PID}" >/dev/null 2>&1 || true
    PERF_PID=""
  fi
}

kill_current_case() {
  local pid
  for pid in "${CURRENT_VICTIM_PIDS[@]:-}"; do
    if [[ -n "${pid}" ]]; then
      kill -INT "${pid}" >/dev/null 2>&1 || true
    fi
  done
  if [[ -n "${CURRENT_ATTACKER_PID:-}" ]]; then
    kill -INT "${CURRENT_ATTACKER_PID}" >/dev/null 2>&1 || true
  fi
}

request_stop() {
  STOP_REQUESTED=1
  echo
  echo "[run] stop requested; cleaning up"
  kill_current_case
  cleanup_tracers
}

cleanup_on_exit() {
  cleanup_tracers
  "${DEPLOY_SCRIPT}" stop >/dev/null 2>&1 || true
}

run_case() {
  local mode="$1"
  local active_count="$2"
  local attacker_rate="$3"
  local ts
  local victim_idx
  local victim_url
  local victim_log
  local attacker_log

  CURRENT_VICTIM_PIDS=()
  CURRENT_ATTACKER_PID=""

  ts="$(date +%s)"
  echo "[run] mode=${mode} victims=${active_count} attacker_rate=${attacker_rate}"
  sync
  echo 3 >/proc/sys/vm/drop_caches

  cleanup_tracers
  reset_tc

  case "${mode}" in
    baseline)
      echo "[run] baseline mode: no eBPF attached"
      ;;
    isolated)
      attach_isolated "${active_count}"
      ;;
    shared)
      prepare_shared
      attach_shared "${active_count}"
      ;;
    *)
      echo "[run] Unknown mode: ${mode}" >&2
      exit 1
      ;;
  esac

  BPFTRACE_PID=""
  PERF_PID=""
  if [[ "${BPFTRACE_AVAILABLE}" -eq 1 ]]; then
    BPFTRACE_OUT="${RAW_DIR}/bpftrace_${mode}_v${active_count}_r${attacker_rate}_${ts}.txt"
    sudo bpftrace "${SCRIPT_DIR}/trace_map_locks.bt" >"${BPFTRACE_OUT}" 2>&1 &
    BPFTRACE_PID="$!"
  fi
  if [[ "${PERF_AVAILABLE}" -eq 1 ]]; then
    PERF_OUT="${RAW_DIR}/perf_${mode}_v${active_count}_r${attacker_rate}_${ts}.data"
    sudo perf record -e 'lock:lock_acquire,lock:lock_released' -a -o "${PERF_OUT}" sleep "${RUN_SECONDS}" >/dev/null 2>&1 &
    PERF_PID="$!"
  fi

  for victim_idx in $(seq 1 "${active_count}"); do
    victim_url="http://$(victim_ip "${victim_idx}"):8080/"
    victim_log="${RAW_DIR}/wrk2_v${victim_idx}_of_${active_count}_${mode}_${attacker_rate}_${ts}.log"
    TARGET_QPS="${VICTIM_QPS}" "${WRK_BIN}" -t"${VICTIM_THREADS}" -c"${VICTIM_CONNECTIONS}" -d"${WORKLOAD_DURATION}" -R"${VICTIM_QPS}" -s "${SCRIPT_DIR}/microsecond_reporter.lua" "${victim_url}" >"${victim_log}" 2>&1 &
    CURRENT_VICTIM_PIDS+=("$!")
  done

  if [[ "${attacker_rate}" -gt 0 ]]; then
    attacker_log="${RAW_DIR}/wrk2_adv_${mode}_v${active_count}_r${attacker_rate}_${ts}.log"
    sudo ip netns exec "$(victim_ns adv)" env TARGET_QPS="${attacker_rate}" "${WRK_BIN}" -t"${VICTIM_THREADS}" -c"${VICTIM_CONNECTIONS}" -d"${WORKLOAD_DURATION}" -R"${attacker_rate}" -s "${SCRIPT_DIR}/microsecond_reporter.lua" "http://$(adv_ns_ip):9090/" >"${attacker_log}" 2>&1 &
    CURRENT_ATTACKER_PID="$!"
  fi

  # Wait for duration while showing progress so user sees activity.
  local elapsed=0
  local total_seconds=${RUN_SECONDS}
  local tick_interval=1
  if [[ ${total_seconds} -ge 20 ]]; then
    tick_interval=5
  fi
  while [[ ${elapsed} -lt ${total_seconds} ]]; do
    if [[ ${STOP_REQUESTED} -eq 1 ]]; then
      return 130
    fi
    show_progress_case "${mode}" "${active_count}" "${attacker_rate}" "${elapsed}" "${total_seconds}"
    sleep "${tick_interval}"
    elapsed=$(( elapsed + tick_interval ))
  done
  if [[ ${STOP_REQUESTED} -eq 1 ]]; then
    return 130
  fi
  # final update to mark case nearing completion
  show_progress_case "${mode}" "${active_count}" "${attacker_rate}" "${total_seconds}" "${total_seconds}"
  printf '\n'

  for victim_idx in $(seq 1 "${active_count}"); do
    wait "${CURRENT_VICTIM_PIDS[$((victim_idx - 1))]}" >/dev/null 2>&1 || true
  done
  if [[ -n "${CURRENT_ATTACKER_PID}" ]]; then
    wait "${CURRENT_ATTACKER_PID}" >/dev/null 2>&1 || true
  fi

  cleanup_tracers
  echo "[run] finished mode=${mode} victims=${active_count} attacker_rate=${attacker_rate}"
}

main() {
  require_root
  require_bin
  mkdir -p "${RAW_DIR}"
  "${DEPLOY_SCRIPT}" start

  # compute total cases for progress tracking
  TOTAL_CASES=0
  for ac in "${ACTIVE_COUNTS[@]}"; do
    for m in "${MODES[@]}"; do
      for ar in "${ATTACKER_RATES[@]}"; do
        TOTAL_CASES=$((TOTAL_CASES + 1))
      done
    done
  done

  trap 'cleanup_on_exit' EXIT
  trap 'request_stop; exit 130' INT TERM

  for active_count in "${ACTIVE_COUNTS[@]}"; do
    for mode in "${MODES[@]}"; do
      for attacker_rate in "${ATTACKER_RATES[@]}"; do
        CASE_NUM=$((CASE_NUM + 1))
        show_progress "Starting ${mode} v${active_count} r${attacker_rate}"
        printf '\n'
        run_case "${mode}" "${active_count}" "${attacker_rate}" || {
          if [[ ${STOP_REQUESTED} -eq 1 ]]; then
            exit 130
          fi
          return 1
        }
        show_progress "Completed ${mode} v${active_count} r${attacker_rate}"
        printf '\n'
        if [[ ${STOP_REQUESTED} -eq 1 ]]; then
          exit 130
        fi
      done
    done
  done

  python3 "${SUMMARY_SCRIPT}" --input-dir "${RAW_DIR}" --output "${RESULT_DIR}/cleaned_matrix_metrics.csv"
  trap - EXIT
  cleanup_on_exit
  echo "[run] sweep complete"
}

main "$@"
