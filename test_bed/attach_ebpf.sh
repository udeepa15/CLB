#!/usr/bin/env bash
# attach_ebpf.sh: Compiles and attaches the eBPF shared-key contention program.
#
# INTERFACES HOOKED:
#   - veth-vic-br  ingress+egress  (host<->victim veth endpoint)
#   - veth-att-br  ingress+egress  (host<->attacker veth endpoint)
#   - br-mesh      ingress+egress  (the bridge itself — catches ALL inter-ns traffic)
#
# WHY br-mesh IS CRITICAL:
#   When ns_attacker sends to 10.0.0.10 (ns_victim), the kernel routes via br-mesh.
#   The bridge forwards the packet internally WITHOUT traversing the host routing stack.
#   Attaching eBPF to br-mesh catches this forwarded traffic and forces it through
#   the shared_global_key spinlock, creating contention with fortio measurement traffic.

set -euo pipefail

SRC_FILE="ebpf_mesh_router.c"
OBJ_FILE="ebpf_mesh_router.o"

if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Must be run as root." >&2; exit 1
fi

for cmd in clang tc; do
    command -v "$cmd" &>/dev/null || { echo "ERROR: '$cmd' not found." >&2; exit 1; }
done

echo "Compiling $SRC_FILE -> $OBJ_FILE..."
clang -g -O2 -target bpf -c "$SRC_FILE" -o "$OBJ_FILE"

echo "Detaching existing filters and cleaning pinned maps..."
for dev in veth-vic-br veth-att-br br-mesh; do
    tc qdisc del dev "$dev" clsact 2>/dev/null || true
done
rm -f /sys/fs/bpf/tc/globals/flow_map /sys/fs/bpf/ip/globals/flow_map       /sys/fs/bpf/flow_map 2>/dev/null || true

echo "Attaching clsact qdiscs..."
for dev in veth-vic-br veth-att-br br-mesh; do
    tc qdisc add dev "$dev" clsact
done

echo "Attaching eBPF filters (ingress + egress) to all three interfaces..."
for dev in veth-vic-br veth-att-br br-mesh; do
    tc filter add dev "$dev" ingress bpf da obj "$OBJ_FILE" sec classifier
    tc filter add dev "$dev" egress  bpf da obj "$OBJ_FILE" sec classifier
    echo "  Attached to $dev"
done

echo "eBPF shared-key contention classifier attached successfully."
