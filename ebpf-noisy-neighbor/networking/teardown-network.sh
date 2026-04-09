#!/usr/bin/env bash
set -euo pipefail

BRIDGE="clb-br0"

if [[ ${EUID} -ne 0 ]]; then
  echo "[teardown-network] Please run as root (sudo)."
  exit 1
fi

echo "[teardown-network] Removing bridge and veth interfaces"
ip link del "$BRIDGE" 2>/dev/null || true

for iface in $(ip -o link show | awk -F': ' '{print $2}' | grep '^veth-' || true); do
  ip link del "$iface" 2>/dev/null || true
done

echo "[teardown-network] Done."
