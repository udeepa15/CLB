#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNC_ROOT="/run/runc-ebpf-noisy-neighbor"

if [[ ${EUID} -ne 0 ]]; then
  echo "[stop-containers] Please run as root (sudo)."
  exit 1
fi

for name in $(runc --root "$RUNC_ROOT" list -f json 2>/dev/null | jq -r '.[].id' || true); do
  echo "[stop-containers] Stopping $name"
  runc --root "$RUNC_ROOT" kill "$name" KILL || true
  runc --root "$RUNC_ROOT" delete "$name" || true
done

"$ROOT_DIR/ebpf/tc/attach.sh" detach || true
"$ROOT_DIR/networking/teardown-network.sh" || true

echo "[stop-containers] Done."
