#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW_DIR="$ROOT_DIR/results/raw"

EXP_NAME="${1:?experiment name required}"
NOISE_LEVEL="${2:?noise level required}"
EBPF_ENABLED="${3:?ebpf true/false required}"
CONTAINERS="${4:?container count required}"
STRATEGY="${5:-none}"
REQUESTS="${6:-300}"

if [[ ${EUID} -ne 0 ]]; then
  echo "[run-single] Please run as root (sudo)."
  exit 1
fi

mkdir -p "$RAW_DIR"

wait_http_ready() {
  local url="$1"
  local retries="${2:-80}"

  for ((i=1; i<=retries; i++)); do
    if curl -s --max-time 1 -o /dev/null "$url"; then
      return 0
    fi
    sleep 0.25
  done

  echo "[run-single] ERROR: Timed out waiting for $url"
  return 1
}

run_clients() {
  local inventory="$ROOT_DIR/containers/runtime/inventory.csv"
  while IFS=, read -r name role ip; do
    [[ "$name" == "name" ]] && continue
    [[ "$role" != "tenant" ]] && continue

    local url="http://${ip}:8080/"
    local prefix="$RAW_DIR/${EXP_NAME}_${name}"

    echo "[run-single] Waiting for $name at $url"
    wait_http_ready "$url"

    echo "[run-single] Collecting latency from $name"
    "$ROOT_DIR/workloads/client.sh" "$url" "$REQUESTS" "$prefix"
  done < "$inventory"
}

cleanup() {
  "$ROOT_DIR/scripts/stop-containers.sh" || true
}

trap cleanup EXIT

echo "[run-single] Starting experiment: $EXP_NAME"
echo "[run-single] noise=$NOISE_LEVEL ebpf=$EBPF_ENABLED containers=$CONTAINERS strategy=$STRATEGY requests=$REQUESTS"

cleanup

CONTAINER_COUNT="$CONTAINERS" NOISE_LEVEL="$NOISE_LEVEL" "$ROOT_DIR/scripts/start-containers.sh"

if [[ "$EBPF_ENABLED" == "true" ]]; then
  echo "[run-single] Enabling eBPF strategy=$STRATEGY"
  "$ROOT_DIR/ebpf/tc/build.sh" "$STRATEGY"
  "$ROOT_DIR/ebpf/tc/attach.sh" attach "$STRATEGY"
else
  echo "[run-single] Baseline mode (no eBPF policy)."
fi

run_clients

"$ROOT_DIR/scripts/collect-metrics.sh" "$EXP_NAME" "$NOISE_LEVEL" "$EBPF_ENABLED" "$STRATEGY" "$CONTAINERS"

echo "[run-single] Experiment complete: $EXP_NAME"
