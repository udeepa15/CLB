#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RAW_DIR="$ROOT_DIR/results/raw"
REQUESTS="${REQUESTS:-300}"
SCENARIO="ebpf"

mkdir -p "$RAW_DIR"

echo "[ebpf] Cleaning previous state"
"$ROOT_DIR/scripts/stop-containers.sh" || true

echo "[ebpf] Starting containers"
"$ROOT_DIR/scripts/start-containers.sh"
sleep 3

echo "[ebpf] Building and attaching eBPF limiter"
"$ROOT_DIR/ebpf/tc/build.sh"
"$ROOT_DIR/ebpf/tc/attach.sh" attach

echo "[ebpf] Running latency clients"
"$ROOT_DIR/workloads/client.sh" "http://10.0.0.2:8080/" "$REQUESTS" "$RAW_DIR/${SCENARIO}_tenant1"
"$ROOT_DIR/workloads/client.sh" "http://10.0.0.3:8080/" "$REQUESTS" "$RAW_DIR/${SCENARIO}_tenant2"

echo "[ebpf] Collecting metrics"
"$ROOT_DIR/scripts/collect-metrics.sh" "$SCENARIO"

echo "[ebpf] Cleaning up"
"$ROOT_DIR/scripts/stop-containers.sh"

echo "[ebpf] Done."
