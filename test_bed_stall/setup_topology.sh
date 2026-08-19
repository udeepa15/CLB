#!/usr/bin/env bash
# setup_topology.sh: Sets up the raw networking data plane.
# MULTI-VICTIM TOPOLOGY (Option A)

set -euo pipefail

BRIDGE="br-mesh"
NS_ATTACKER="ns_attacker"
ATTACKER_IP="10.0.0.20/24"
BRIDGE_IP="10.0.0.1/24"

# 3 Victims simulating 3 separate Pods
VICTIMS=( "ns_victim1:10.0.0.10/24:veth-vic1-br:veth-victim1" 
          "ns_victim2:10.0.0.11/24:veth-vic2-br:veth-victim2" 
          "ns_victim3:10.0.0.12/24:veth-vic3-br:veth-victim3" )

cleanup() {
    echo "Cleaning up..."
    ip netns delete "$NS_ATTACKER" 2>/dev/null || true
    ip link delete veth-att-br 2>/dev/null || true
    ip link delete "$BRIDGE" 2>/dev/null || true
    for vic in "${VICTIMS[@]}"; do
        IFS=':' read -r ns ip_addr br_veth ns_veth <<< "$vic"
        ip netns delete "$ns" 2>/dev/null || true
        ip link delete "$br_veth" 2>/dev/null || true
    done
}

if [ "$EUID" -ne 0 ]; then echo "ERROR: Must be root."; exit 1; fi
if [ "${1:-}" = "cleanup" ]; then cleanup; exit 0; fi
cleanup

echo "Creating host bridge $BRIDGE..."
ip link add name "$BRIDGE" type bridge
ip addr add "$BRIDGE_IP" dev "$BRIDGE"
ip link set dev "$BRIDGE" up

echo "Creating Attacker namespace..."
ip netns add "$NS_ATTACKER"
ip link add name veth-attacker type veth peer name veth-att-br
ip link set dev veth-att-br master "$BRIDGE"
ip link set dev veth-attacker netns "$NS_ATTACKER"
ip netns exec "$NS_ATTACKER" ip addr add "$ATTACKER_IP" dev veth-attacker
ip netns exec "$NS_ATTACKER" ip link set dev veth-attacker up
ip netns exec "$NS_ATTACKER" ip link set dev lo up
ip netns exec "$NS_ATTACKER" ip route add default via 10.0.0.1
ip link set dev veth-att-br up

echo "Creating 3 Victim namespaces..."
for vic in "${VICTIMS[@]}"; do
    IFS=':' read -r ns ip_addr br_veth ns_veth <<< "$vic"
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
echo "Multi-victim Topology setup complete."
