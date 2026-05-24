#!/usr/bin/env bash
set -euo pipefail
# run_full_sweep.sh - single command to clean, sweep, and aggregate results with live progress
# Usage: run_full_sweep.sh [--tenants "1,3,5"] [--rates "0,5000,...,40000"] [--duration 60]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$REPO_ROOT/ebpf_research"
RESULTS="$ROOT/results"

# Defaults
TENANTS="1,3,5"
RATES="0,5000,10000,15000,20000,25000,30000,35000,40000"
DURATION=60
SEED=1000000

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tenants) TENANTS="$2"; shift 2;;
    --rates) RATES="$2"; shift 2;;
    --duration) DURATION="$2"; shift 2;;
    --seed) SEED="$2"; shift 2;;
    *) echo "Unknown arg: $1"; exit 1;;
  esac
done

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║           REDIS QUEUE TESTBED - FULL SWEEP RUNNER             ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "Configuration:"
echo "  Tenants:       $TENANTS"
echo "  Attack rates:  $RATES"
echo "  Duration/run:  ${DURATION}s"
echo "  Queue seed:    $SEED items"
echo ""

# Step 1: Cleanup
echo "[1/4] Cleaning up old namespaces and processes..."
for ns in $(ip netns list 2>/dev/null | awk '{print $1}' || true); do
  sudo ip netns delete "$ns" 2>/dev/null || true
done
sudo pkill -9 redis-server 2>/dev/null || true
sleep 2
echo "      ✓ Cleanup done"
echo ""

# Step 2: Start sweep
echo "[2/4] Starting sweep..."
SWEEP_LOG="$RESULTS/sweep_v2.log"
SWEEP_START=$(date +%s)

nohup bash "$SCRIPT_DIR/sweep_queue_matrix.sh" \
  --tenants "$TENANTS" \
  --attacker_rates "$RATES" \
  --duration "$DURATION" \
  --seed "$SEED" \
  > "$SWEEP_LOG" 2>&1 &
SWEEP_PID=$!

echo "      PID: $SWEEP_PID"
echo "      Log: $SWEEP_LOG"
echo ""

# Step 3: Monitor progress
echo "[3/4] Monitoring progress..."
echo "      (Press Ctrl+C to stop monitoring; sweep will continue)"
echo ""

POLL_COUNT=0
while kill -0 $SWEEP_PID 2>/dev/null; do
  POLL_COUNT=$((POLL_COUNT + 1))
  
  # Show sweep log status every ~30s (15 iterations of 2s sleep)
  if [ $((POLL_COUNT % 15)) -eq 0 ]; then
    # Get line count to show activity
    if [ -f "$SWEEP_LOG" ]; then
      LINE_COUNT=$(wc -l < "$SWEEP_LOG" 2>/dev/null || echo "0")
      echo "      [$(date +%H:%M:%S)] $LINE_COUNT log lines - sweep in progress..."
    fi
  fi
  sleep 2
done

SWEEP_END=$(date +%s)
SWEEP_ELAPSED=$((SWEEP_END - SWEEP_START))
SWEEP_ELAPSED_MIN=$((SWEEP_ELAPSED / 60))

wait $SWEEP_PID || true
echo ""
echo "      ✓ Sweep completed in ${SWEEP_ELAPSED_MIN}m ${SWEEP_ELAPSED}s"
echo ""

# Step 4: Aggregate and show results
echo "[4/4] Aggregating results..."
RESULTS_CSV="$REPO_ROOT/sweep_results_v2.csv"

python3 "$SCRIPT_DIR/aggregate_sweep_results.py" \
  --results-dir "$RESULTS" \
  --output-csv "$RESULTS_CSV"

echo "      ✓ Aggregated to: $RESULTS_CSV"
echo ""

# Summary
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                        SWEEP COMPLETE                         ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "Results Summary:"
echo "  CSV file:            $RESULTS_CSV"
echo "  Sweep logs:          $RESULTS/t*_r*_*/worker_*.log"
echo "  Bpftrace captures:   $RESULTS/t*_r*_*/bpftrace_redis_dict.log"
echo ""
echo "Next steps:"
echo "  1. View CSV:"
echo "     head -20 $RESULTS_CSV"
echo ""
echo "  2. View worker RESULT lines:"
echo "     grep RESULT: $RESULTS/t*/worker_*.log"
echo ""
echo "  3. View bpftrace Redis latencies:"
echo "     less $RESULTS/t*/bpftrace_redis_dict.log"
echo ""
echo "  4. Analyze in Python:"
echo "     import pandas as pd"
echo "     df = pd.read_csv('$RESULTS_CSV')"
echo "     df.groupby('attacker_rate')['throughput_mps'].mean()"
echo ""
