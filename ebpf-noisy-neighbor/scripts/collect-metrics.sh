#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 5 ]]; then
  echo "Usage: $0 <experiment_name> <noise_level> <ebpf_enabled> <strategy> <containers>"
  exit 1
fi

EXPERIMENT_NAME="$1"
NOISE_LEVEL="$2"
EBPF_ENABLED="$3"
STRATEGY="$4"
CONTAINERS="$5"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW_DIR="$ROOT_DIR/results/raw"
PROC_DIR="$ROOT_DIR/results/processed"
SUMMARY_CSV="$PROC_DIR/summary.csv"
RESULTS_CSV="$PROC_DIR/results.csv"
INVENTORY_FILE="$ROOT_DIR/containers/runtime/inventory.csv"

mkdir -p "$RAW_DIR" "$PROC_DIR"

if [[ ! -f "$SUMMARY_CSV" ]]; then
  echo "timestamp,scenario,tenant,requests,failures,p99_ms" > "$SUMMARY_CSV"
fi

if [[ ! -f "$RESULTS_CSV" ]]; then
  echo "timestamp,experiment,noise_level,ebpf_enabled,strategy,containers,tenant,requests,failures,p50_ms,p95_ms,p99_ms,jitter_ms,throughput_rps,packet_drops,isolation_score" > "$RESULTS_CSV"
fi

packet_drops="0"
if [[ "$EBPF_ENABLED" == "true" ]]; then
  packet_drops="$("$ROOT_DIR/ebpf/tc/attach.sh" drops 2>/dev/null || echo 0)"
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
  throughput="$(parse_field "$summary_line" throughput_rps)"

  ts="$(date --iso-8601=seconds)"
  scenario="baseline"
  if [[ "$EBPF_ENABLED" == "true" ]]; then
    scenario="ebpf"
  fi

  echo "$ts,$scenario,$name,$requests,$failures,$p99" >> "$SUMMARY_CSV"
  echo "$ts,$EXPERIMENT_NAME,$NOISE_LEVEL,$EBPF_ENABLED,$STRATEGY,$CONTAINERS,$name,$requests,$failures,$p50,$p95,$p99,$jitter,$throughput,$packet_drops,na" >> "$RESULTS_CSV"
  echo "[collect-metrics] $EXPERIMENT_NAME/$name p99=$p99 jitter=$jitter throughput=$throughput"
done < "$INVENTORY_FILE"

# Fill isolation_score based on baseline_low_noise rows for each tenant.
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
    if r.get("experiment") == "baseline_low_noise":
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
