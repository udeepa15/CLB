#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$REPO_ROOT/ebpf_research"
RESULT_DIR="$ROOT_DIR/results/raw"
mkdir -p "$RESULT_DIR"

check_cmd(){
  command -v "$1" >/dev/null 2>&1 || { echo "$1 not found; please install."; exit 1; }
}

check_cmd clang || true
check_cmd bpftool || true
check_cmd fortio || true
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
}

trap cleanup EXIT

attach_tc(){
  local dev="$1"
  local obj="$2"
  sudo tc qdisc add dev "$dev" clsact 2>/dev/null || true
  sudo tc filter add dev "$dev" ingress bpf da obj "$obj" sec classifier || true
}

detach_tc(){
  local dev="$1"
  sudo tc filter del dev "$dev" ingress 2>/dev/null || true
}

create_pinned_map(){
  local pinpath="/sys/fs/bpf/shared_counter_map"
  if [ ! -e "$pinpath" ]; then
    sudo bpftool map create "$pinpath" type hash key 4 value 8 entries 1024 name shared_counter_map
  fi
}

modes=(baseline isolated shared)
attacker_rates=(0 10000 20000 40000 60000 80000)

ATTACKER_TOOL="fortio"

rm -f "$RESULT_DIR"/wrk_adv_* "$RESULT_DIR"/attacker_fortio_* "$RESULT_DIR"/fortio_v*.json

bash "$SCRIPT_DIR/deploy_workloads.sh" start

for mode in "${modes[@]}"; do
  echo "Starting mode: $mode"
  if [ "$mode" = "shared" ]; then
    create_pinned_map
    build_bpf
    for i in 1 2 3 4 5 6; do
      attach_tc "veth_v${i}" "$REPO_ROOT/bpf/counter_tc.o" || true
    done
  elif [ "$mode" = "isolated" ]; then
    build_bpf
    for i in 1 2 3 4 5 6; do
      attach_tc "veth_v${i}" "$REPO_ROOT/bpf/counter_tc.o" || true
    done
  else
    echo "Baseline: no eBPF attached"
  fi

  for rate in "${attacker_rates[@]}"; do
    ts=$(date +%s)
    echo "Run mode=$mode attacker_rate=$rate"
    sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'
    victim_pids=()
    attacker_pid=""

    # start bpftrace capture (if available)
    if [ "$BPFTRACE_AVAILABLE" -eq 1 ]; then
      BT_OUT="$RESULT_DIR/bpftrace_${mode}_${rate}_${ts}.txt"
      sudo bpftrace -e 'kprobe:bpf_map_lookup_elem { @t[tid] = nsecs; } kretprobe:bpf_map_lookup_elem { @[tid] = hist(nsecs - @t[tid]); }' >"$BT_OUT" 2>&1 &
      BPFTRACE_PID=$!
    else
      BPFTRACE_PID=""
    fi

    # start perf capture (15s sample at start) if available
    if [ "$PERF_AVAILABLE" -eq 1 ]; then
      PERF_OUT="$RESULT_DIR/perf_${mode}_${rate}_${ts}.data"
      sudo perf record -e 'lock:lock_acquire,lock:lock_released' -a -o "$PERF_OUT" sleep 15 &
    fi

    # start victim traffic: 5 fortio processes each 200 RPS to reach combined ~1000 RPS
    for i in 1 2 3 4 5; do
      TARGET="http://10.200.${i}.2:8080"
      OUT="$RESULT_DIR/fortio_v${i}_${mode}_${rate}_${ts}.json"
      LOG="$RESULT_DIR/fortio_v${i}_${mode}_${rate}_${ts}.log"
      fortio load -qps 200 -c 50 -t 60 -json "$OUT" "$TARGET" >"$LOG" 2>&1 &
      victim_pids+=("$!")
    done

    # start attacker traffic in namespace
    if [ "$rate" -gt 0 ]; then
      ATT_OUT="$RESULT_DIR/attacker_${ATTACKER_TOOL}_${mode}_${rate}_${ts}.json"
      if [ "$ATTACKER_TOOL" = "fortio" ]; then
        ATT_LOG="$RESULT_DIR/attacker_${ATTACKER_TOOL}_${mode}_${rate}_${ts}.log"
        fortio load -qps "$rate" -c 50 -t 60 -json "$ATT_OUT" http://10.200.6.2:9090/ >"$ATT_LOG" 2>&1 &
        attacker_pid="$!"
      else
        sudo ip netns exec v_netns_adv "$ATTACKER_TOOL" -t1 -c100 -d60 -R "$rate" http://10.200.6.2:9090/ >"$ATT_OUT" 2>&1 &
        attacker_pid="$!"
      fi
    fi

    # wait for workload duration plus small buffer
    sleep 65

    for pid in "${victim_pids[@]}"; do
      wait "$pid" 2>/dev/null || true
    done
    if [ -n "$attacker_pid" ]; then
      wait "$attacker_pid" 2>/dev/null || true
    fi

    if ! ls "$RESULT_DIR"/fortio_v*_${mode}_${rate}_${ts}.json >/dev/null 2>&1; then
      echo "No victim JSON files were created for mode=$mode rate=$rate" >&2
    fi

    # stop bpftrace if it was started
    if [ -n "$BPFTRACE_PID" ]; then
      sudo kill $BPFTRACE_PID 2>/dev/null || true
    fi

    echo "Finished run mode=$mode rate=$rate"
  done

done

echo "Sweep complete. Results in $RESULT_DIR"
