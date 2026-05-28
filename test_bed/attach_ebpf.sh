#!/usr/bin/env bash
# attach_ebpf.sh: Attaches to all interfaces including all 3 victims.

set -euo pipefail

SRC_FILE="ebpf_mesh_router.c"
OBJ_FILE="ebpf_mesh_router.o"
INTERFACES=("veth-att-br" "br-mesh" "veth-vic1-br" "veth-vic2-br" "veth-vic3-br")

if [ "$EUID" -ne 0 ]; then echo "ERROR: Must be root."; exit 1; fi

clang -g -O2 -target bpf -c "$SRC_FILE" -o "$OBJ_FILE"

echo "Detaching existing filters..."
for dev in "${INTERFACES[@]}"; do tc qdisc del dev "$dev" clsact 2>/dev/null || true; done
rm -f /sys/fs/bpf/tc/globals/flow_map /sys/fs/bpf/ip/globals/flow_map /sys/fs/bpf/flow_map 2>/dev/null || true

echo "Attaching clsact qdiscs and eBPF filters..."
for dev in "${INTERFACES[@]}"; do
    tc qdisc add dev "$dev" clsact
    tc filter add dev "$dev" ingress bpf da obj "$OBJ_FILE" sec classifier
    tc filter add dev "$dev" egress  bpf da obj "$OBJ_FILE" sec classifier
    echo "  Attached to $dev"
done
