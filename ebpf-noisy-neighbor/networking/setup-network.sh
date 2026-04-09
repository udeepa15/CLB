#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNC_ROOT="/run/runc-ebpf-noisy-neighbor"
BRIDGE="clb-br0"
BRIDGE_IP="10.0.0.1/24"
INVENTORY_FILE="$ROOT_DIR/containers/runtime/inventory.csv"

need_root() {
  if [[ ${EUID} -ne 0 ]]; then
    echo "[setup-network] Please run as root (sudo)."
    exit 1
  fi
}

container_pid() {
  local name="$1"
  runc --root "$RUNC_ROOT" state "$name" 2>/dev/null | jq -r '.pid'
}

setup_bridge() {
  if ! ip link show "$BRIDGE" &>/dev/null; then
    echo "[setup-network] Creating bridge $BRIDGE"
    ip link add "$BRIDGE" type bridge
  fi

  if ! ip addr show dev "$BRIDGE" | grep -q "10.0.0.1/24"; then
    ip addr add "$BRIDGE_IP" dev "$BRIDGE" || true
  fi

  ip link set "$BRIDGE" up
}

setup_veth_for_container() {
  local name="$1"
  local c_ip="$2"
  local pid
  pid="$(container_pid "$name")"

  if [[ -z "$pid" || "$pid" == "null" ]]; then
    echo "[setup-network] ERROR: container '$name' is not running"
    exit 1
  fi

  local host_if="veth-${name}"
  local cont_if="eth0-${name}"

  ip link del "$host_if" 2>/dev/null || true

  echo "[setup-network] Creating veth pair for $name"
  ip link add "$host_if" type veth peer name "$cont_if"

  ip link set "$host_if" master "$BRIDGE"
  ip link set "$host_if" up

  ip link set "$cont_if" netns "$pid"
  nsenter -t "$pid" -n ip link set lo up
  nsenter -t "$pid" -n ip link set "$cont_if" name eth0
  nsenter -t "$pid" -n ip addr add "$c_ip" dev eth0
  nsenter -t "$pid" -n ip link set eth0 up
  nsenter -t "$pid" -n ip route add default via 10.0.0.1
}

need_root
setup_bridge

if [[ ! -f "$INVENTORY_FILE" ]]; then
  echo "[setup-network] ERROR: Missing runtime inventory: $INVENTORY_FILE"
  exit 1
fi

while IFS=, read -r name role ip; do
  [[ "$name" == "name" ]] && continue
  setup_veth_for_container "$name" "${ip}/24"
done < "$INVENTORY_FILE"

sysctl -w net.ipv4.ip_forward=1 >/dev/null

echo "[setup-network] Network is ready."
