#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$REPO_ROOT/ebpf_research"
mkdir -p "$ROOT_DIR"/results/raw

if ! mountpoint -q /sys/fs/bpf; then
  sudo mount -t bpf none /sys/fs/bpf
fi

NETNS=(v_netns1 v_netns2 v_netns3 v_netns4 v_netns5 v_netns_adv)

for ns in "${NETNS[@]}"; do
  sudo ip netns del "$ns" 2>/dev/null || true
  sudo ip netns add "$ns"
done

i=1
for ns in "${NETNS[@]}"; do
  HOST_VETH="veth_v${i}"
  CHILD_VETH="veth_child${i}"
  sudo ip link del "$HOST_VETH" 2>/dev/null || true
  sudo ip link add "$HOST_VETH" type veth peer name "$CHILD_VETH"
  sudo ip link set "$CHILD_VETH" netns "$ns"
  HOST_IP="10.200.${i}.1/24"
  CHILD_IP="10.200.${i}.2/24"
  sudo ip addr add "$HOST_IP" dev "$HOST_VETH" || true
  sudo ip link set "$HOST_VETH" up
  sudo ip netns exec "$ns" ip addr add "$CHILD_IP" dev "$CHILD_VETH"
  sudo ip netns exec "$ns" ip link set "$CHILD_VETH" up
  sudo ip netns exec "$ns" ip link set lo up
  sudo ip netns exec "$ns" ip route add default via 10.200.${i}.1 || true
  i=$((i+1))
done

sudo sysctl -w net.ipv4.ip_forward=1

CGROUP_ROOT=/sys/fs/cgroup
if ! mountpoint -q "$CGROUP_ROOT"; then
  sudo mount -t cgroup2 none "$CGROUP_ROOT"
fi

ATTACKER_CG="$CGROUP_ROOT/attacker.slice"
sudo mkdir -p "$ATTACKER_CG"
echo "20000 100000" | sudo tee "$ATTACKER_CG/cpu.max" >/dev/null

mkdir -p "$ROOT_DIR/bpf"
echo "Infrastructure setup complete. Root: $ROOT_DIR"
