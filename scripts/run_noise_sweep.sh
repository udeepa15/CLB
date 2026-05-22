#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$REPO_ROOT/ebpf_research"
RESULT_DIR="$ROOT_DIR/results/raw"
PLOT_DIR="$ROOT_DIR/results/plots"
mkdir -p "$RESULT_DIR" "$PLOT_DIR"

for dir in "$ROOT_DIR" "$ROOT_DIR/results" "$RESULT_DIR" "$PLOT_DIR"; do
  if [ ! -d "$dir" ]; then
    echo "Required directory missing: $dir" >&2
    exit 1
  fi
done

check_cmd(){
  command -v "$1" >/dev/null 2>&1 || { echo "$1 not found; please install."; exit 1; }
}

check_cmd clang || true
check_cmd bpftool || true
# optional tools
if ! command -v perf >/dev/null 2>&1; then
  echo "perf not available; perf recordings will be skipped"
  PERF_AVAILABLE=0
else
  PERF_AVAILABLE=1
fi
if ! command -v bpftrace >/dev/null 2>&1; then
  echo "bpftrace not available; bpftrace captures will be skipped"
  BPFTRACE_AVAILABLE=0
else
  BPFTRACE_AVAILABLE=1
fi

# Verify perf supports the lock tracepoints we need; if not, skip perf
if [ "$PERF_AVAILABLE" -eq 1 ]; then
  if [ ! -e /sys/kernel/tracing/events/lock/lock_acquire ]; then
    if ! perf list 2>/dev/null | grep -q 'lock:lock_acquire'; then
      echo "perf present but lock tracepoints are unavailable; perf recordings will be skipped"
      PERF_AVAILABLE=0
    fi
  fi
fi

build_bpf(){
  echo "Building BPF object"
  make -C "$REPO_ROOT" || { echo "make failed"; exit 1; }
}

cleanup(){
  bash "$SCRIPT_DIR/deploy_workloads.sh" stop >/dev/null 2>&1 || true
  for i in 1 2 3 4 5 6; do
    detach_tc "veth_v${i}" || true
  done
  sudo rm -f /sys/fs/bpf/shared_counter_map >/dev/null 2>&1 || true
}

trap cleanup EXIT

attach_tc(){
  local dev="$1"
  local obj="$2"
  sudo tc qdisc add dev "$dev" clsact 2>/dev/null || true
  sudo tc filter add dev "$dev" ingress bpf da obj "$obj" sec classifier || true
}

prepare_veth_execution_paths(){
  local obj="$1"
  for i in 1 2 3 4 5 6; do
    local dev="veth_v${i}"
    sudo ip link set dev "$dev" up 2>/dev/null || true
    sudo tc qdisc replace dev "$dev" clsact 2>/dev/null || sudo tc qdisc add dev "$dev" clsact 2>/dev/null || true
    sudo tc filter add dev "$dev" ingress bpf da obj "$obj" sec classifier 2>/dev/null || true
    sudo tc qdisc show dev "$dev" >/dev/null 2>&1 || {
      echo "Warning: unable to confirm qdisc on $dev" >&2
    }
  done
}

detach_tc(){
  local dev="$1"
  sudo tc filter del dev "$dev" ingress 2>/dev/null || true
}

create_pinned_map(){
  local pinpath="/sys/fs/bpf/shared_counter_map"
  sudo rm -f "$pinpath" >/dev/null 2>&1 || true
  sudo bpftool map create "$pinpath" type hash key 4 value 8 entries 1024 name shared_counter_map
}

modes=(baseline isolated shared)
victim_counts=(1 3 5)
attacker_rates=(0 10000 20000 25000 27500 30000 32500 35000 37500 40000 42500 45000)

WRK_BIN="${WRK_BIN:-wrk2}"
VICTIM_QPS="${VICTIM_QPS:-200}"
VICTIM_CONNECTIONS="${VICTIM_CONNECTIONS:-100}"
VICTIM_THREADS="${VICTIM_THREADS:-2}"
WORKLOAD_DURATION="${WORKLOAD_DURATION:-60s}"

if ! command -v "$WRK_BIN" >/dev/null 2>&1; then
  echo "$WRK_BIN not found; please install it or set WRK_BIN to a valid binary." >&2
  exit 1
fi

rm -f "$RESULT_DIR"/fortio_* "$RESULT_DIR"/attacker_fortio_* "$RESULT_DIR"/wrk_adv_* "$RESULT_DIR"/wrk2_v* "$RESULT_DIR"/wrk2_adv_* "$RESULT_DIR"/bpftrace_*

bash "$SCRIPT_DIR/deploy_workloads.sh" restart

for v_count in "${victim_counts[@]}"; do
  echo "=== Beginning Experiment Matrix: Active Victims = ${v_count} ==="

  for mode in "${modes[@]}"; do
    echo "Starting mode: $mode with $v_count victims"
    if [ "$mode" = "shared" ]; then
      create_pinned_map
      build_bpf
      prepare_veth_execution_paths "$REPO_ROOT/bpf/counter_tc.o"
    elif [ "$mode" = "isolated" ]; then
      build_bpf
      prepare_veth_execution_paths "$REPO_ROOT/bpf/counter_tc.o"
    else
      echo "Baseline: no eBPF attached"
    fi

    for rate in "${attacker_rates[@]}"; do
      ts=$(date +%s)
      echo "Run matrix: mode=$mode | victims=$v_count | attacker_rate=$rate"
      sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'
      victim_pids=()
      attacker_pid=""

      BPFTRACE_OUT="$RESULT_DIR/bpftrace_${mode}_v${v_count}_r${rate}_${ts}.txt"
      if [ "$BPFTRACE_AVAILABLE" -eq 1 ]; then
        sudo bpftrace "$SCRIPT_DIR/trace_map_locks.bt" >"$BPFTRACE_OUT" 2>&1 &
        BPFTRACE_PID=$!
      else
        BPFTRACE_PID=""
      fi

      sleep 1

      if [ "$PERF_AVAILABLE" -eq 1 ]; then
        PERF_OUT="$RESULT_DIR/perf_${mode}_v${v_count}_r${rate}_${ts}.data"
        sudo perf record -e 'lock:lock_acquire,lock:lock_released' -a -o "$PERF_OUT" sleep 15 &
      fi

      for ((i=1; i<=v_count; i++)); do
        TARGET="http://10.200.${i}.2:8080"
        LOG="$RESULT_DIR/wrk2_v${i}_of_${v_count}_${mode}_${rate}_${ts}.log"
        TARGET_QPS="$VICTIM_QPS" "$WRK_BIN" -t"$VICTIM_THREADS" -c"$VICTIM_CONNECTIONS" -d"$WORKLOAD_DURATION" -R"$VICTIM_QPS" -s "$SCRIPT_DIR/microsecond_reporter.lua" "$TARGET" >"$LOG" 2>&1 &
        victim_pids+=("$!")
      done

      if [ "$rate" -gt 0 ]; then
        ATT_LOG="$RESULT_DIR/wrk2_adv_${mode}_v${v_count}_r${rate}_${ts}.log"
        sudo ip netns exec v_netns_adv env TARGET_QPS="$rate" "$WRK_BIN" -t"$VICTIM_THREADS" -c"$VICTIM_CONNECTIONS" -d"$WORKLOAD_DURATION" -R"$rate" -s "$SCRIPT_DIR/microsecond_reporter.lua" http://10.200.6.2:9090/ >"$ATT_LOG" 2>&1 &
        attacker_pid="$!"
      fi

      sleep 60

      for pid in "${victim_pids[@]}"; do
        wait "$pid" 2>/dev/null || true
      done
      if [ -n "$attacker_pid" ]; then
        wait "$attacker_pid" 2>/dev/null || true
      fi

      if [ -n "$BPFTRACE_PID" ]; then
        sudo kill -INT "$BPFTRACE_PID" 2>/dev/null || true
        wait "$BPFTRACE_PID" 2>/dev/null || true
      fi

      echo "Finished run mode=$mode victims=$v_count rate=$rate"
    done
  done
done

echo "Sweep complete. Results in $RESULT_DIR"
