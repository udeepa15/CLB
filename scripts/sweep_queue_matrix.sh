#!/usr/bin/env bash
set -euo pipefail
# sweep_queue_matrix.sh - sweep over tenant_count x attacker_rate matrix
# Usage: sweep_queue_matrix.sh --tenants "1 3 5" --attacker_rates "0 10000 20000"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$REPO_ROOT/ebpf_research"
RESULTS="$ROOT/results"
mkdir -p "$RESULTS"

# archive prior logs/csvs to avoid cross-run pollution
ARCHIVE_DIR="$RESULTS/archive_$(date +%s)"
mkdir -p "$ARCHIVE_DIR"
shopt -s nullglob
for f in "$RESULTS"/*.csv "$RESULTS"/*.log; do
  if [ "$(basename "$f")" = "sweep_v2.log" ]; then
    continue
  fi
  mv "$f" "$ARCHIVE_DIR/" || true
done
for d in "$RESULTS"/m*_t*_r_* "$RESULTS"/t*_r_*; do
  [ -d "$d" ] && mv "$d" "$ARCHIVE_DIR/" || true
done
shopt -u nullglob

MODES=(sidecar sidecarless_ebpf)
TENANTS=(1 3 5)
ATTACKER_RATES=(0 10000 20000 30000 40000)
DURATION=60
SEED_ITEMS=1000000

while [[ $# -gt 0 ]]; do
  case "$1" in
    --modes) IFS="," read -r -a MODES <<< "$2"; shift 2;;
    --tenants) IFS="," read -r -a TENANTS <<< "$2"; shift 2;;
    --attacker_rates) IFS="," read -r -a ATTACKER_RATES <<< "$2"; shift 2;;
    --duration) DURATION="$2"; shift 2;;
    --seed) SEED_ITEMS="$2"; shift 2;;
    *) echo "Unknown arg $1"; exit 1;;
  esac
done

echo "Sweep modes:${MODES[*]} tenants:${TENANTS[*]} rates:${ATTACKER_RATES[*]} duration:${DURATION}s seed:${SEED_ITEMS}"

# ensure broker
echo "Starting broker"
sudo bash "$SCRIPT_DIR/manage_broker.sh" start

# verify redis-cli is available
if ! command -v redis-cli >/dev/null 2>&1; then
  echo "redis-cli is required for queue flush and seeding" >&2
  exit 1
fi

cleanup_iteration() {
  if [ -n "${BPFTRACE_PID:-}" ]; then
    sudo kill "$BPFTRACE_PID" 2>/dev/null || true
    BPFTRACE_PID=""
  fi
  sudo pkill -f "adv_storm.py" 2>/dev/null || true
  sudo pkill -f "queue_worker.py" 2>/dev/null || true
  sudo pkill -f "socat TCP-LISTEN:6379" 2>/dev/null || true
  if command -v bpftool >/dev/null 2>&1; then
    sudo bpftool prog detach pinned /sys/fs/bpf/redis_sockops/bpf_redis_redirect msg_verdict pinned /sys/fs/bpf/redis_sockops/redis_sock_map 2>/dev/null || true
    sudo bpftool cgroup detach /sys/fs/cgroup sock_ops pinned /sys/fs/bpf/redis_sockops/bpf_sockmap_ctrl 2>/dev/null || true
    sudo rm -rf /sys/fs/bpf/redis_sockops 2>/dev/null || true
  fi
}

trap cleanup_iteration EXIT

for mode in "${MODES[@]}"; do
  for t in "${TENANTS[@]}"; do
    for r in "${ATTACKER_RATES[@]}"; do
      stamp="m${mode}_t${t}_r${r}_$(date +%s)"
      outdir="$RESULTS/$stamp"
      mkdir -p "$outdir"
      echo "Running sweep: mode=$mode tenants=$t attacker_rate=$r -> $outdir"

      echo "Resetting Redis queues"
      redis-cli -h 10.200.0.1 FLUSHALL >/dev/null
      python3 "$SCRIPT_DIR/seed_queues.py" --broker-ip 10.200.0.1 --num-queues "$t" --total-items "$SEED_ITEMS"

      # start bpftrace if available (background capture of Redis dict operations)
      BPFTRACE_PID=""
      if command -v bpftrace >/dev/null 2>&1; then
        echo "Starting bpftrace to capture Redis latencies"
        sudo bpftrace "$SCRIPT_DIR/trace_redis_dict.bt" > "$outdir/bpftrace_redis_dict.log" 2>&1 &
        BPFTRACE_PID=$!
        sleep 1  # give bpftrace time to attach
      fi

      # deploy tenants and start workers
      bash "$SCRIPT_DIR/deploy_queue_workloads.sh" --mode "$mode" --num-tenants "$t" --duration "$DURATION" --broker-ip 10.200.0.1

      # start adversary (host namespace) if r>0
      if [ "$r" -gt 0 ]; then
        nohup python3 "$SCRIPT_DIR/adv_storm.py" --broker-ip 10.200.0.1 --rate "$r" --duration "$DURATION" --queue-name tenant_queue_v1 > "$outdir/adv_storm.log" 2>&1 &
      fi

      # wait for sweep duration
      sleep "$DURATION"

      cleanup_iteration

      # collect results: worker logs contain RESULT lines
      cp "$ROOT/results"/worker_*.log "$outdir/" 2>/dev/null || true
      [ -f "$ROOT/results/redis_broker.log" ] && cp "$ROOT/results/redis_broker.log" "$outdir/" || true

      echo "Completed sweep $stamp"
    done
  done
done

echo "Stopping broker"
sudo bash "$SCRIPT_DIR/manage_broker.sh" stop
