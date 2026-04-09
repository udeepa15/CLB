#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNC_ROOT="/run/runc-ebpf-noisy-neighbor"
RUNTIME_DIR="$ROOT_DIR/containers/runtime"
INVENTORY_FILE="$RUNTIME_DIR/inventory.csv"

CONTAINER_COUNT="${CONTAINER_COUNT:-3}"
NOISE_LEVEL="${NOISE_LEVEL:-medium}"
TRAFFIC_PATTERN="${TRAFFIC_PATTERN:-constant}"
FAILURE_MODE="${FAILURE_MODE:-none}"

need_root() {
  if [[ ${EUID} -ne 0 ]]; then
    echo "[start-containers] Please run as root (sudo)."
    exit 1
  fi
}

start_one() {
  local name="$1"
  local bundle="$RUNTIME_DIR/$name"

  if [[ ! -d "$bundle/rootfs" || ! -f "$bundle/config.json" ]]; then
    echo "[start-containers] Bundle missing for $name. Running setup-rootfs first..."
    bash "$ROOT_DIR/containers/setup-rootfs.sh"
  fi

  if runc --root "$RUNC_ROOT" state "$name" >/dev/null 2>&1; then
    echo "[start-containers] Container $name already exists; deleting"
    runc --root "$RUNC_ROOT" kill "$name" KILL || true
    runc --root "$RUNC_ROOT" delete "$name" || true
  fi

  echo "[start-containers] Starting $name"
  runc --root "$RUNC_ROOT" run -d --bundle "$bundle" "$name"
}

prepare_runtime() {
  if [[ ! -d "$ROOT_DIR/containers/alpine-rootfs/rootfs-template" ]]; then
    echo "[start-containers] Rootfs template missing. Running setup-rootfs first..."
    bash "$ROOT_DIR/containers/setup-rootfs.sh"
  fi

  echo "[start-containers] Generating runtime bundles for ${CONTAINER_COUNT} containers (noise=${NOISE_LEVEL}, pattern=${TRAFFIC_PATTERN}, failure=${FAILURE_MODE})"
  TRAFFIC_PATTERN="$TRAFFIC_PATTERN" FAILURE_MODE="$FAILURE_MODE" bash "$ROOT_DIR/scripts/generate-runtime.sh" "$CONTAINER_COUNT" "$NOISE_LEVEL"
}

need_root
mkdir -p "$RUNC_ROOT"

prepare_runtime

while IFS=, read -r name role ip; do
  [[ "$name" == "name" ]] && continue
  start_one "$name"
done < "$INVENTORY_FILE"

echo "[start-containers] Configuring networking"
bash "$ROOT_DIR/networking/setup-network.sh"

echo "[start-containers] Container states:"
runc --root "$RUNC_ROOT" list

echo "[start-containers] Done."
