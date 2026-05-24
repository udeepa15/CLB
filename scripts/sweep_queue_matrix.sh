#!/usr/bin/env bash
set -euo pipefail
# sweep_queue_matrix.sh - sweep over tenant_count x attacker_rate matrix
# Usage: sweep_queue_matrix.sh --tenants "1 3 5" --attacker_rates "0 10000 20000"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$REPO_ROOT/ebpf_research"
RESULTS="$ROOT/results"
mkdir -p "$RESULTS"

TENANTS=(1 3 5)
ATTACKER_RATES=(0 10000 20000)
DURATION=30
SEED_ITEMS=1000000

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tenants) IFS="," read -r -a TENANTS <<< "$2"; shift 2;;
    --attacker_rates) IFS="," read -r -a ATTACKER_RATES <<< "$2"; shift 2;;
    --duration) DURATION="$2"; shift 2;;
    --seed) SEED_ITEMS="$2"; shift 2;;
    *) echo "Unknown arg $1"; exit 1;;
  esac
done

echo "Sweep tenants:${TENANTS[*]} rates:${ATTACKER_RATES[*]} duration:${DURATION}s seed:${SEED_ITEMS}"

# ensure broker
echo "Starting broker"
sudo bash "$SCRIPT_DIR/manage_broker.sh" start

# seed queues
echo "Seeding queues"
MAX_TENANTS="${TENANTS[0]}"
for t in "${TENANTS[@]}"; do
  if [ "$t" -gt "$MAX_TENANTS" ]; then
    MAX_TENANTS="$t"
  fi
done
python3 "$SCRIPT_DIR/seed_queues.py" --broker-ip 10.200.0.1 --num-queues "$MAX_TENANTS" --total-items "$SEED_ITEMS"

for t in "${TENANTS[@]}"; do
  for r in "${ATTACKER_RATES[@]}"; do
    stamp="t${t}_r${r}_$(date +%s)"
    outdir="$RESULTS/$stamp"
    mkdir -p "$outdir"
    echo "Running sweep: tenants=$t attacker_rate=$r -> $outdir"

    # start bpftrace if available (background capture of Redis dict operations)
    BPFTRACE_PID=""
    if command -v bpftrace >/dev/null 2>&1; then
      echo "Starting bpftrace to capture Redis latencies"
      sudo bpftrace "$SCRIPT_DIR/trace_redis_dict.bt" > "$outdir/bpftrace_redis_dict.log" 2>&1 &
      BPFTRACE_PID=$!
      sleep 1  # give bpftrace time to attach
    fi

    # deploy tenants (no sidecars) and start workers
    bash "$SCRIPT_DIR/deploy_queue_workloads.sh" --num-tenants "$t" --duration "$DURATION" --broker-ip 10.200.0.1

    # start adversary (host namespace) if r>0
    if [ "$r" -gt 0 ]; then
      nohup python3 "$SCRIPT_DIR/adv_storm.py" --broker-ip 10.200.0.1 --rate "$r" --duration "$DURATION" --queue-name tenant_queue_v1 > "$outdir/adv_storm.log" 2>&1 &
    fi

    # wait for sweep duration
    sleep "$DURATION"

    # stop bpftrace if running
    if [ -n "$BPFTRACE_PID" ]; then
      echo "Stopping bpftrace (PID $BPFTRACE_PID)"
      sudo kill "$BPFTRACE_PID" 2>/dev/null || true
      sleep 1
    fi

    # collect results: worker logs contain RESULT lines
    cp "$ROOT/results"/worker_*.log "$outdir/" 2>/dev/null || true
    [ -f "$ROOT/results/redis_broker.log" ] && cp "$ROOT/results/redis_broker.log" "$outdir/" || true

    echo "Completed sweep $stamp"
  done
done

echo "Stopping broker"
sudo bash "$SCRIPT_DIR/manage_broker.sh" stop
