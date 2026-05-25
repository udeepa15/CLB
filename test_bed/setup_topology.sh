#!/usr/bin/env bash
# setup_topology.sh: Sets up the raw networking data plane for the namespace lab.
#
# Network Routing Logic:
# - A host-side bridge 'br-mesh' acts as the virtual switch connecting the namespaces.
# - Two network namespaces are created: 'ns_victim' (target HTTP server) and 'ns_attacker' (client load generator).
# - Veth pairs connect each namespace's interface to the host-side bridge:
#     - ns_victim: veth-victim (in netns) <---> veth-vic-br (attached to bridge br-mesh)
#     - ns_attacker: veth-attacker (in netns) <---> veth-att-br (attached to bridge br-mesh)
# - Host-side bridge IP is 10.0.0.1.
# - ns_victim IP is 10.0.0.10, routing all default traffic through 10.0.0.1.
# - ns_attacker IP is 10.0.0.20, routing all default traffic through 10.0.0.1.
# - IP forwarding is enabled on the host to allow routing between interfaces if needed.

set -euo pipefail

BRIDGE="br-mesh"
NS_VICTIM="ns_victim"
NS_ATTACKER="ns_attacker"

# IP assignments
BRIDGE_IP="10.0.0.1/24"
VICTIM_IP="10.0.0.10/24"
ATTACKER_IP="10.0.0.20/24"

cleanup() {
    echo "Cleaning up network namespaces and bridge to prevent cross-run pollution..."
    ip netns delete "$NS_VICTIM" 2>/dev/null || true
    ip netns delete "$NS_ATTACKER" 2>/dev/null || true
    ip link delete "$BRIDGE" 2>/dev/null || true
    ip link delete veth-vic-br 2>/dev/null || true
    ip link delete veth-att-br 2>/dev/null || true
}

# Ensure script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run as root to manage network namespaces and bridges." >&2
    exit 1
fi

# Parse command line args (allow standalone cleanup)
if [ "${1:-}" = "cleanup" ]; then
    cleanup
    exit 0
fi

# Clean up any existing instances first
cleanup

echo "Creating host bridge $BRIDGE..."
ip link add name "$BRIDGE" type bridge
ip addr add "$BRIDGE_IP" dev "$BRIDGE"
ip link set dev "$BRIDGE" up

echo "Creating namespaces: $NS_VICTIM and $NS_ATTACKER..."
ip netns add "$NS_VICTIM"
ip netns add "$NS_ATTACKER"

echo "Creating veth pairs..."
# Setup veth for Victim
ip link add name veth-victim type veth peer name veth-vic-br
# Setup veth for Attacker
ip link add name veth-attacker type veth peer name veth-att-br

echo "Wiring veth endpoints to bridge..."
ip link set dev veth-vic-br master "$BRIDGE"
ip link set dev veth-att-br master "$BRIDGE"

echo "Moving container-side endpoints into their namespaces..."
ip link set dev veth-victim netns "$NS_VICTIM"
ip link set dev veth-attacker netns "$NS_ATTACKER"

echo "Configuring Victim namespace ($NS_VICTIM)..."
ip netns exec "$NS_VICTIM" ip addr add "$VICTIM_IP" dev veth-victim
ip netns exec "$NS_VICTIM" ip link set dev veth-victim up
ip netns exec "$NS_VICTIM" ip link set dev lo up
ip netns exec "$NS_VICTIM" ip route add default via 10.0.0.1

echo "Configuring Attacker namespace ($NS_ATTACKER)..."
ip netns exec "$NS_ATTACKER" ip addr add "$ATTACKER_IP" dev veth-attacker
ip netns exec "$NS_ATTACKER" ip link set dev veth-attacker up
ip netns exec "$NS_ATTACKER" ip link set dev lo up
ip netns exec "$NS_ATTACKER" ip route add default via 10.0.0.1

echo "Bringing up host-side veth endpoints..."
ip link set dev veth-vic-br up
ip link set dev veth-att-br up

echo "Enabling host IP forwarding..."
sysctl -w net.ipv4.ip_forward=1 >/dev/null

echo "Topology setup complete successfully."
