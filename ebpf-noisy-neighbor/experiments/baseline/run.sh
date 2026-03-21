#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RAW_DIR="$ROOT_DIR/results/raw"
REQUESTS="${REQUESTS:-300}"
SCENARIO="baseline"

mkdir -p "$RAW_DIR"

echo "[baseline] Cleaning previous state"
"$ROOT_DIR/scripts/stop-containers.sh" || true

echo "[baseline] Starting containers"
"$ROOT_DIR/scripts/start-containers.sh"
sleep 3

echo "[baseline] Running latency clients"
"$ROOT_DIR/workloads/client.sh" "http://10.0.0.2:8080/" "$REQUESTS" "$RAW_DIR/${SCENARIO}_tenant1"
"$ROOT_DIR/workloads/client.sh" "http://10.0.0.3:8080/" "$REQUESTS" "$RAW_DIR/${SCENARIO}_tenant2"

echo "[baseline] Collecting metrics"
"$ROOT_DIR/scripts/collect-metrics.sh" "$SCENARIO"

echo "[baseline] Stopping containers"
"$ROOT_DIR/scripts/stop-containers.sh"

echo "[baseline] Done."
