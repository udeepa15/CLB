#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 11 ]]; then
  echo "Usage: $0 <experiment_name> <noise_level> <ebpf_enabled> <strategy> <containers> <isolation_method> <traffic_pattern> <identity_mode> <iteration> <failure_mode> <p99_threshold_ms>"
  exit 1
fi

EXPERIMENT_NAME="$1"
NOISE_LEVEL="$2"
EBPF_ENABLED="$3"
STRATEGY="$4"
CONTAINERS="$5"
ISOLATION_METHOD="$6"
TRAFFIC_PATTERN="$7"
IDENTITY_MODE="$8"
ITERATION="$9"
FAILURE_MODE="${10}"
P99_THRESHOLD_MS="${11}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW_DIR="$ROOT_DIR/results/raw"
PROC_DIR="$ROOT_DIR/results/processed"
SUMMARY_CSV="$PROC_DIR/summary.csv"
RESULTS_CSV="$PROC_DIR/results.csv"
DIST_CSV="$PROC_DIR/latency_distribution.csv"
INVENTORY_FILE="$ROOT_DIR/containers/runtime/inventory.csv"

mkdir -p "$RAW_DIR" "$PROC_DIR"

if [[ ! -f "$SUMMARY_CSV" ]]; then
  echo "timestamp,scenario,tenant,requests,failures,p99_ms" > "$SUMMARY_CSV"
fi

if [[ ! -f "$RESULTS_CSV" ]]; then
  echo "timestamp,experiment,noise_level,traffic_pattern,isolation_method,identity_mode,ebpf_enabled,strategy,containers,iteration,failure_mode,tenant,requests,failures,p50_ms,p95_ms,p99_ms,jitter_ms,variance_ms2,throughput_rps,packet_drops,tail_amplification,recovery_window_samples,degradation_window_samples,isolation_score" > "$RESULTS_CSV"
fi

if [[ ! -f "$DIST_CSV" ]]; then
  echo "experiment,tenant,bucket_ms,count" > "$DIST_CSV"
fi

packet_drops="0"
if [[ "$EBPF_ENABLED" == "true" ]]; then
  packet_drops="$("$ROOT_DIR/ebpf/tc/attach.sh" drops "$STRATEGY" 2>/dev/null || echo 0)"
  packet_drops="${packet_drops:-0}"
fi

parse_field() {
  local line="$1"
  local key="$2"
  echo "$line" | sed -n "s/.*${key}=\([^ ]*\).*/\1/p"
}

while IFS=, read -r name role ip; do
  [[ "$name" == "name" ]] && continue
  [[ "$role" != "tenant" ]] && continue

  log_file="$RAW_DIR/${EXPERIMENT_NAME}_${name}.log"
  summary_line="$(grep '^SUMMARY ' "$log_file" | tail -n1 || true)"
  if [[ -z "$summary_line" ]]; then
    echo "[collect-metrics] WARNING: missing SUMMARY for ${EXPERIMENT_NAME}/${name}"
    continue
  fi

  requests="$(parse_field "$summary_line" requests)"
  failures="$(parse_field "$summary_line" failures)"
  p50="$(parse_field "$summary_line" p50_ms)"
  p95="$(parse_field "$summary_line" p95_ms)"
  p99="$(parse_field "$summary_line" p99_ms)"
  jitter="$(parse_field "$summary_line" jitter_ms)"
  variance="$(parse_field "$summary_line" variance_ms2)"
  throughput="$(parse_field "$summary_line" throughput_rps)"

  [[ -z "$variance" ]] && variance="nan"

  tail_amp="nan"
  if [[ -n "$p50" && "$p50" != "0" && "$p50" != "nan" && "$p99" != "nan" ]]; then
    tail_amp="$(python3 - <<PY
p50=float("$p50")
p99=float("$p99")
print(f"{(p99/p50):.4f}" if p50 > 0 else "nan")
PY
)"
  fi

  recovery_window="na"
  degradation_window="na"
  ts_file="$RAW_DIR/${EXPERIMENT_NAME}_${name}.timeseries.csv"
  if [[ -f "$ts_file" ]]; then
    windows="$(python3 - "$ts_file" "$P99_THRESHOLD_MS" <<'PY'
import csv
import sys

ts_file = sys.argv[1]
thr = float(sys.argv[2])

series = []
with open(ts_file, 'r', encoding='utf-8') as f:
    r = csv.DictReader(f)
    for row in r:
        try:
            series.append(float(row['latency_ms']))
        except Exception:
            continue

if not series:
    print("na,na")
    raise SystemExit(0)

worst_deg = 0
cur = 0
for v in series:
    if v > thr:
        cur += 1
        worst_deg = max(worst_deg, cur)
    else:
        cur = 0

recover = 0
for i in range(len(series) - 1, -1, -1):
    if series[i] <= thr:
        recover += 1
    else:
        break

print(f"{recover},{worst_deg}")
PY
)"
    recovery_window="${windows%%,*}"
    degradation_window="${windows##*,}"
  fi

  ts="$(date --iso-8601=seconds)"
  scenario="baseline"
  if [[ "$EBPF_ENABLED" == "true" ]]; then
    scenario="ebpf"
  fi

  echo "$ts,$scenario,$name,$requests,$failures,$p99" >> "$SUMMARY_CSV"
  echo "$ts,$EXPERIMENT_NAME,$NOISE_LEVEL,$TRAFFIC_PATTERN,$ISOLATION_METHOD,$IDENTITY_MODE,$EBPF_ENABLED,$STRATEGY,$CONTAINERS,$ITERATION,$FAILURE_MODE,$name,$requests,$failures,$p50,$p95,$p99,$jitter,$variance,$throughput,$packet_drops,$tail_amp,$recovery_window,$degradation_window,na" >> "$RESULTS_CSV"
  echo "[collect-metrics] $EXPERIMENT_NAME/$name p99=$p99 jitter=$jitter throughput=$throughput"

  lat_file="$RAW_DIR/${EXPERIMENT_NAME}_${name}.latency"
  if [[ -f "$lat_file" ]]; then
    python3 - "$lat_file" "$EXPERIMENT_NAME" "$name" >> "$DIST_CSV" <<'PY'
import sys

path, experiment, tenant = sys.argv[1:4]
buckets = [0, 1, 2, 5, 10, 20, 50, 100, 200]
counts = {b: 0 for b in buckets}
counts["200+"] = 0

with open(path, 'r', encoding='utf-8') as f:
    for line in f:
        if line.startswith('#'):
            continue
        parts = line.strip().rsplit(',', 1)
        if len(parts) != 2:
            continue
        try:
            v = float(parts[1])
        except Exception:
            continue
        placed = False
        for b in buckets:
            if v <= b:
                counts[b] += 1
                placed = True
                break
        if not placed:
            counts["200+"] += 1

for b in buckets:
    print(f"{experiment},{tenant},{b},{counts[b]}")
print(f"{experiment},{tenant},200+,{counts['200+']}")
PY
  fi
done < "$INVENTORY_FILE"

python3 - "$RESULTS_CSV" <<'PY'
import csv
from pathlib import Path

path = Path(__import__('sys').argv[1])
rows = []
with path.open("r", encoding="utf-8", newline="") as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    for row in reader:
        rows.append(row)

baseline = {}
for r in rows:
    if r.get("isolation_method") == "none" and r.get("noise_level") == "low":
        try:
            baseline[r["tenant"]] = float(r["p99_ms"])
        except Exception:
            pass

for r in rows:
    tenant = r.get("tenant")
    try:
        p99 = float(r.get("p99_ms", "nan"))
    except Exception:
        r["isolation_score"] = "na"
        continue

    b = baseline.get(tenant)
    if b is None or b <= 0:
        r["isolation_score"] = "na"
    else:
        r["isolation_score"] = f"{p99 / b:.4f}"

with path.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
PY

echo "[collect-metrics] Updated: $SUMMARY_CSV"
echo "[collect-metrics] Updated: $RESULTS_CSV"
