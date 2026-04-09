#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RAW_DIR="$ROOT_DIR/results/raw"
PROC_RESULTS="$ROOT_DIR/results/processed/results.csv"
RAW_RESULTS="$RAW_DIR/results.csv"

EXP_NAME="${1:-map_pressure_$(date +%Y%m%d_%H%M%S)}"
NOISE_LEVEL="${NOISE_LEVEL:-high}"
REQUESTS="${REQUESTS:-800}"
CONTAINERS="${CONTAINERS:-3}"
TARGET_UPS="${TARGET_UPDATES_PER_SEC:-100000}"
DURATION_S="${MAP_CHURN_DURATION_S:-35}"

if [[ ${EUID} -ne 0 ]]; then
  echo "[map-pressure] Please run as root (sudo)."
  exit 1
fi

mkdir -p "$RAW_DIR"
CHURN_REPORT="$RAW_DIR/${EXP_NAME}_map_churn_report.csv"
LOOKUP_CSV="$RAW_DIR/${EXP_NAME}_map_lookup_latency.csv"

cleanup() {
  if [[ -n "${CHURN_PID:-}" ]]; then
    kill "$CHURN_PID" 2>/dev/null || true
    wait "$CHURN_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "[map-pressure] Starting experiment $EXP_NAME"

echo "[map-pressure] Running scenario with adaptive+cgroup to exercise map churn path"
HOOK_POINT="tc" "$ROOT_DIR/scripts/run-single-experiment.sh" \
  "$EXP_NAME" "$NOISE_LEVEL" true "$CONTAINERS" adaptive "$REQUESTS" \
  adaptive bursty cgroup 1 none 8.0 \
  80 512 auto 60 384 auto "" 0 0 0 tc &
RUN_PID="$!"

# Wait until adaptive maps appear, then start high-rate churn.
for _ in {1..120}; do
  if bpftool -j map show 2>/dev/null | grep -q '"name":"cgroup_policy_map"'; then
    break
  fi
  sleep 0.5
done

python3 "$ROOT_DIR/scripts/map_churn_worker.py" \
  --map-name cgroup_policy_map \
  --duration "$DURATION_S" \
  --target-updates-per-sec "$TARGET_UPS" \
  --report "$CHURN_REPORT" &
CHURN_PID="$!"

wait "$RUN_PID"
wait "$CHURN_PID" || true

# Victim lookup latency probe (tenant1 by convention in this framework).
python3 "$ROOT_DIR/scripts/map_lookup_latency.py" \
  --experiment "$EXP_NAME" \
  --tenant tenant1 \
  --map-name cgroup_policy_map \
  --samples 60000 \
  --key-u64 1 \
  --out "$LOOKUP_CSV"

# Backfill map_lookup_latency_us into results rows for this experiment/tenant.
python3 - "$PROC_RESULTS" "$RAW_RESULTS" "$LOOKUP_CSV" "$EXP_NAME" <<'PY'
import csv
import sys
from pathlib import Path

proc_path = Path(sys.argv[1])
raw_path = Path(sys.argv[2])
lookup = Path(sys.argv[3])
exp = sys.argv[4]

avg = "na"
tenant = "tenant1"
if lookup.exists():
    with lookup.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        row = next(r, None)
        if row:
            avg = row.get("map_lookup_latency_us", "na")
            tenant = row.get("tenant", "tenant1")

for path in [proc_path, raw_path]:
    if not path.exists():
        continue
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
        fields = rows[0].keys() if rows else []

    changed = False
    for row in rows:
        if row.get("experiment") == exp and row.get("tenant") == tenant:
            row["map_lookup_latency_us"] = avg
            changed = True

    if changed:
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(fields))
            w.writeheader()
            w.writerows(rows)
PY

echo "[map-pressure] Completed."
echo "[map-pressure] churn_report=$CHURN_REPORT"
echo "[map-pressure] lookup_metrics=$LOOKUP_CSV"
