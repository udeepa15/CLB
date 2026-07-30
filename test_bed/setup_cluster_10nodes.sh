#!/usr/bin/env bash
# setup_cluster_10nodes.sh: Sets up a 10-node network cluster topology.
# Node 1 (ns_node1: 10.0.0.10) is the victim node.
# Nodes 2-10 (ns_node2..ns_node10: 10.0.0.11..10.0.0.19) act as cluster nodes / potential noisy neighbors.

set -euo pipefail

BRIDGE="br-mesh"
BRIDGE_IP="10.0.0.1/24"
NUM_NODES=10

cleanup() {
    echo "Cleaning up 10-node topology..."
    ip link delete "$BRIDGE" 2>/dev/null || true
    for i in $(seq 1 $NUM_NODES); do
        ip netns delete "ns_node$i" 2>/dev/null || true
        ip link delete "veth-node$i-br" 2>/dev/null || true
    done
}

if [ "$EUID" -ne 0 ]; then echo "ERROR: Must be root."; exit 1; fi
if [ "${1:-}" = "cleanup" ]; then cleanup; exit 0; fi
cleanup

echo "Creating host bridge $BRIDGE..."
ip link add name "$BRIDGE" type bridge
ip addr add "$BRIDGE_IP" dev "$BRIDGE"
ip link set dev "$BRIDGE" up

echo "Creating $NUM_NODES node namespaces..."
for i in $(seq 1 $NUM_NODES); do
    ns="ns_node$i"
    ip_addr="10.0.0.$((9 + i))/24"
    br_veth="veth-node$i-br"
    ns_veth="veth-node$i"

    ip netns add "$ns"
    ip link add name "$ns_veth" type veth peer name "$br_veth"
    ip link set dev "$br_veth" master "$BRIDGE"
    ip link set dev "$ns_veth" netns "$ns"
    ip netns exec "$ns" ip addr add "$ip_addr" dev "$ns_veth"
    ip netns exec "$ns" ip link set dev "$ns_veth" up
    ip netns exec "$ns" ip link set dev lo up
    ip netns exec "$ns" ip route add default via 10.0.0.1
    ip link set dev "$br_veth" up
done

echo "Enabling host IP forwarding..."
sysctl -w net.ipv4.ip_forward=1 >/dev/null
echo "10-Node Cluster Topology setup complete."
