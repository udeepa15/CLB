#!/usr/bin/env bash
# attach_ebpf.sh: Compiles and attaches the eBPF router program to the host-side veth interfaces.
#
# Routing & eBPF interception details:
# - Traffic Control (TC) 'clsact' queuing discipline is loaded onto host-side veth endpoints.
# - The eBPF program is attached to both 'ingress' (incoming to host from namespace)
#   and 'egress' (outgoing from host to namespace) for:
#     - veth-vic-br (Victim interface host side)
#     - veth-att-br (Attacker interface host side)
# - Direct Action (da) mode is specified, which allows the eBPF program to make decisions
#   and return TC action codes directly (e.g. TC_ACT_OK) without invoking additional TC actions.

set -euo pipefail

SRC_FILE="ebpf_mesh_router.c"
OBJ_FILE="ebpf_mesh_router.o"

# Ensure script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run as root to attach eBPF filters." >&2
    exit 1
fi

# Ensure compiler and tools are present
for cmd in clang tc; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: Required host tool '$cmd' is not installed." >&2
        exit 1
    fi
done

echo "Compiling eBPF program $SRC_FILE to $OBJ_FILE..."
clang -g -O2 -target bpf -c "$SRC_FILE" -o "$OBJ_FILE"

echo "Detaching any existing filters and cleaning up pinned maps..."
tc qdisc del dev veth-vic-br clsact 2>/dev/null || true
tc qdisc del dev veth-att-br clsact 2>/dev/null || true

# Clean up pinned maps to ensure we start from a clean state (avoid map-reuse pollution)
rm -f /sys/fs/bpf/tc/globals/flow_map 2>/dev/null || true
rm -f /sys/fs/bpf/ip/globals/flow_map 2>/dev/null || true
rm -f /sys/fs/bpf/flow_map 2>/dev/null || true

echo "Attaching clsact qdiscs to veth-vic-br and veth-att-br..."
tc qdisc add dev veth-vic-br clsact
tc qdisc add dev veth-att-br clsact

echo "Attaching ingress & egress eBPF filters to veth-vic-br..."
tc filter add dev veth-vic-br ingress bpf da obj "$OBJ_FILE" sec classifier
tc filter add dev veth-vic-br egress bpf da obj "$OBJ_FILE" sec classifier

echo "Attaching ingress & egress eBPF filters to veth-att-br..."
tc filter add dev veth-att-br ingress bpf da obj "$OBJ_FILE" sec classifier
tc filter add dev veth-att-br egress bpf da obj "$OBJ_FILE" sec classifier

echo "eBPF map-contention router successfully compiled and attached."
