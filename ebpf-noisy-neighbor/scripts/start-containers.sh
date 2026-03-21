#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNC_ROOT="/run/runc-ebpf-noisy-neighbor"
RUNTIME_DIR="$ROOT_DIR/containers/runtime"

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
    "$ROOT_DIR/containers/setup-rootfs.sh"
  fi

  if runc --root "$RUNC_ROOT" state "$name" >/dev/null 2>&1; then
    echo "[start-containers] Container $name already exists; deleting"
    runc --root "$RUNC_ROOT" kill "$name" KILL || true
    runc --root "$RUNC_ROOT" delete "$name" || true
  fi

  echo "[start-containers] Starting $name"
  runc --root "$RUNC_ROOT" run -d --bundle "$bundle" "$name"
}

need_root
mkdir -p "$RUNC_ROOT"

start_one tenant1
start_one tenant2
start_one noisy

echo "[start-containers] Configuring networking"
"$ROOT_DIR/networking/setup-network.sh"

echo "[start-containers] Container states:"
runc --root "$RUNC_ROOT" list

echo "[start-containers] Done."
