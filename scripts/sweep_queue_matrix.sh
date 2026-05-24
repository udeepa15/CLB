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
sudo $SCRIPT_DIR/manage_broker.sh start

# seed queues
echo "Seeding queues"
python3 $SCRIPT_DIR/seed_queues.py --broker-ip 10.200.0.1 --num-queues ${TENANTS[-1]} --total-items "$SEED_ITEMS"

for t in "${TENANTS[@]}"; do
  for r in "${ATTACKER_RATES[@]}"; do
    stamp="t${t}_r${r}_$(date +%s)"
    outdir="$RESULTS/$stamp"
    mkdir -p "$outdir"
    echo "Running sweep: tenants=$t attacker_rate=$r -> $outdir"

    # deploy tenants (no sidecars) and start workers
    $SCRIPT_DIR/deploy_queue_workloads.sh --num-tenants "$t" --duration "$DURATION" --broker-ip 10.200.0.1

    # start adversary (host namespace) if r>0
    if [ "$r" -gt 0 ]; then
      nohup python3 $SCRIPT_DIR/adv_storm.py --broker-ip 10.200.0.1 --rate "$r" --duration "$DURATION" --queue-name tenant_queue_v1 > "$outdir/adv_storm.log" 2>&1 &
    fi

    # wait for sweep duration
    sleep "$DURATION"

    # collect results: worker logs contain RESULT lines
    cp $ROOT/results/worker_*.log "$outdir/" || true
    [ -f "$ROOT/results/redis_broker.log" ] && cp "$ROOT/results/redis_broker.log" "$outdir/" || true

    echo "Completed sweep $stamp"
  done
done

echo "Stopping broker"
sudo $SCRIPT_DIR/manage_broker.sh stop
