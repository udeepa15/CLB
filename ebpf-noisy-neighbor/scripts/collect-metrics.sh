#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <scenario-name>"
  exit 1
fi

SCENARIO="$1"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW_DIR="$ROOT_DIR/results/raw"
PROC_DIR="$ROOT_DIR/results/processed"
SUMMARY_CSV="$PROC_DIR/summary.csv"

mkdir -p "$RAW_DIR" "$PROC_DIR"

if [[ ! -f "$SUMMARY_CSV" ]]; then
  echo "timestamp,scenario,tenant,requests,failures,p99_ms" > "$SUMMARY_CSV"
fi

for tenant in tenant1 tenant2; do
  log_file="$RAW_DIR/${SCENARIO}_${tenant}.log"
  if [[ ! -f "$log_file" ]]; then
    echo "[collect-metrics] WARNING: Missing log file $log_file"
    continue
  fi

  summary_line="$(grep '^SUMMARY ' "$log_file" | tail -n1 || true)"
  if [[ -z "$summary_line" ]]; then
    echo "[collect-metrics] WARNING: Missing SUMMARY in $log_file"
    continue
  fi

  requests="$(echo "$summary_line" | sed -n 's/.*requests=\([^ ]*\).*/\1/p')"
  failures="$(echo "$summary_line" | sed -n 's/.*failures=\([^ ]*\).*/\1/p')"
  p99="$(echo "$summary_line" | sed -n 's/.*p99_ms=\([^ ]*\).*/\1/p')"

  ts="$(date --iso-8601=seconds)"
  echo "$ts,$SCENARIO,$tenant,$requests,$failures,$p99" >> "$SUMMARY_CSV"
  echo "[collect-metrics] $SCENARIO/$tenant -> p99_ms=$p99"
done

echo "[collect-metrics] Updated: $SUMMARY_CSV"
