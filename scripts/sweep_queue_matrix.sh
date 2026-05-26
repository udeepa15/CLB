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

MODES=(sidecar sidecarless)
TENANTS=(1 3 5)
ATTACKER_RATES=(0 10000 20000 30000 40000)
DURATION=60
SEED_ITEMS=1000000
BROKER_IP=10.200.0.1
ADV_PID=""

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

TOTAL_STEPS=$((1 + ${#MODES[@]} * ${#TENANTS[@]} * ${#ATTACKER_RATES[@]} + 1))
CURRENT_STEP=0

render_progress() {
  local current="$1"
  local total="$2"
  local label="$3"
  local width=24
  local filled=$(( current * width / total ))
  local bar=""
  local i

  for ((i = 0; i < filled; i++)); do
    bar+="#"
  done
  for ((i = filled; i < width; i++)); do
    bar+="-"
  done

  printf '\n[%s] %d/%d %s\n' "$bar" "$current" "$total" "$label"
}

advance_progress() {
  CURRENT_STEP=$((CURRENT_STEP + 1))
  render_progress "$CURRENT_STEP" "$TOTAL_STEPS" "$1"
}

# ensure broker
advance_progress "Starting broker"
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

  # Reap explicit adversary job first to avoid bash "Killed" job notifications.
  if [ -n "${ADV_PID:-}" ]; then
    sudo kill -9 "$ADV_PID" 2>/dev/null || true
    wait "$ADV_PID" 2>/dev/null || true
    ADV_PID=""
  fi

  for pattern in 'adv_storm.py' 'queue_worker.py' 'socat TCP-LISTEN:6379'; do
    local pids
    pids=$(pgrep -f "$pattern" || true)
    if [ -n "$pids" ]; then
      sudo kill -9 $pids 2>/dev/null || true
    fi
  done
}

trap cleanup_iteration EXIT

for mode in "${MODES[@]}"; do
  for t in "${TENANTS[@]}"; do
    for r in "${ATTACKER_RATES[@]}"; do
      advance_progress "Running mode=$mode tenants=$t attacker_rate=$r"
      stamp="m${mode}_t${t}_r${r}_$(date +%s)"
      outdir="$RESULTS/$stamp"
      mkdir -p "$outdir"
      echo "Output directory: $outdir"

      echo "Resetting Redis queues"
      redis-cli -h "$BROKER_IP" FLUSHALL >/dev/null
      python3 "$SCRIPT_DIR/seed_queues.py" --broker-ip "$BROKER_IP" --num-queues "$t" --total-items "$SEED_ITEMS"

      # start bpftrace if available (background capture of Redis dict operations)
      BPFTRACE_PID=""
      if command -v bpftrace >/dev/null 2>&1; then
        echo "Starting bpftrace to capture Redis latencies"
        sudo bpftrace "$SCRIPT_DIR/trace_redis_dict.bt" > "$outdir/bpftrace_redis_dict.log" 2>&1 &
        BPFTRACE_PID=$!
        sleep 1  # give bpftrace time to attach
      fi

      # deploy tenants and start workers
      bash "$SCRIPT_DIR/deploy_queue_workloads.sh" --mode "$mode" --num-tenants "$t" --duration "$DURATION" --broker-ip "$BROKER_IP" --output-dir "$outdir"

      # start adversary (host namespace) if r>0
      if [ "$r" -gt 0 ]; then
        nohup python3 "$SCRIPT_DIR/adv_storm.py" --broker-ip "$BROKER_IP" --rate "$r" --duration "$DURATION" --queue-name tenant_queue_v1 > "$outdir/adv_storm.log" 2>&1 &
        ADV_PID=$!
      else
        ADV_PID=""
      fi

      # wait for sweep duration
      sleep "$DURATION"

      cleanup_iteration

      # collect broker log after workers have been stopped
      [ -f "$ROOT/results/redis_broker.log" ] && cp "$ROOT/results/redis_broker.log" "$outdir/" || true

      echo "Completed sweep $stamp"
    done
  done
done

advance_progress "Stopping broker"
sudo bash "$SCRIPT_DIR/manage_broker.sh" stop
