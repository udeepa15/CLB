#!/usr/bin/env bash
# attach_ebpf.sh: Compiler & attacher for naive and stallhide eBPF limiters.

set -euo pipefail

VARIANT="${1:-naive}"

if [ "$VARIANT" = "stallhide" ]; then
    SRC_FILE="ebpf_limiter_stallhide.c"
    OBJ_FILE="ebpf_limiter_stallhide.o"
else
    SRC_FILE="ebpf_limiter_naive.c"
    OBJ_FILE="ebpf_limiter_naive.o"
fi

INTERFACES=("veth-att-br" "br-mesh" "veth-vic1-br" "veth-vic2-br" "veth-vic3-br")

if [ "$EUID" -ne 0 ]; then echo "ERROR: Must be root."; exit 1; fi

echo "Compiling $SRC_FILE -> $OBJ_FILE..."
clang -g -O2 -target bpf -c "$SRC_FILE" -o "$OBJ_FILE"

echo "Detaching existing filters..."
for dev in "${INTERFACES[@]}"; do tc qdisc del dev "$dev" clsact 2>/dev/null || true; done
rm -f /sys/fs/bpf/tc/globals/flow_map /sys/fs/bpf/tc/globals/lock_latency_hist /sys/fs/bpf/tc/globals/limiter_only_latency_hist /sys/fs/bpf/tc/globals/update_counter_map /sys/fs/bpf/tc/globals/percpu_rate_limit_map 2>/dev/null || true

echo "Attaching clsact qdiscs and eBPF filters for variant: $VARIANT..."
for dev in "${INTERFACES[@]}"; do
    tc qdisc add dev "$dev" clsact
    tc filter add dev "$dev" ingress bpf da obj "$OBJ_FILE" sec classifier
    tc filter add dev "$dev" egress  bpf da obj "$OBJ_FILE" sec classifier
    echo "  Attached to $dev"
done
