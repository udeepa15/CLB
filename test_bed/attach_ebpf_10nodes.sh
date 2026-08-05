#!/usr/bin/env bash
# attach_ebpf_10nodes.sh: Attaches eBPF mesh router to all 10 node interfaces.

set -euo pipefail

SRC_FILE="ebpf_mesh_router.c"
OBJ_FILE="ebpf_mesh_router.o"
NUM_NODES=10

INTERFACES=("br-mesh")
for i in $(seq 1 $NUM_NODES); do
    INTERFACES+=("veth-node$i-br")
done

if [ "$EUID" -ne 0 ]; then echo "ERROR: Must be root."; exit 1; fi

clang -g -O2 -target bpf -c "$SRC_FILE" -o "$OBJ_FILE"

echo "Detaching existing 10-node eBPF filters..."
for dev in "${INTERFACES[@]}"; do tc qdisc del dev "$dev" clsact 2>/dev/null || true; done
rm -f /sys/fs/bpf/tc/globals/flow_map /sys/fs/bpf/tc/globals/lock_latency_hist /sys/fs/bpf/tc/globals/update_counter_map /sys/fs/bpf/ip/globals/flow_map /sys/fs/bpf/flow_map 2>/dev/null || true

echo "Attaching clsact qdiscs and eBPF filters across 10 nodes..."
for dev in "${INTERFACES[@]}"; do
    tc qdisc add dev "$dev" clsact
    tc filter add dev "$dev" ingress bpf da obj "$OBJ_FILE" sec classifier
    tc filter add dev "$dev" egress  bpf da obj "$OBJ_FILE" sec classifier
    echo "  Attached eBPF to $dev"
done
