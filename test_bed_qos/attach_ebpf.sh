#!/usr/bin/env bash
# attach_ebpf.sh: Flexible eBPF compiler & attacher for sidecarless, qos_tiered, qos_dynamic, and qos_auto.

set -euo pipefail

MODE="${1:-sidecarless}"

if [ "$MODE" = "qos_tiered" ]; then
    SRC_FILE="ebpf_qos_tiered.c"
    OBJ_FILE="ebpf_qos_tiered.o"
elif [ "$MODE" = "qos_dynamic" ]; then
    SRC_FILE="ebpf_qos_dynamic.c"
    OBJ_FILE="ebpf_qos_dynamic.o"
elif [ "$MODE" = "qos_auto" ]; then
    SRC_FILE="ebpf_qos_auto.c"
    OBJ_FILE="ebpf_qos_auto.o"
else
    SRC_FILE="ebpf_mesh_router.c"
    OBJ_FILE="ebpf_mesh_router.o"
fi

INTERFACES=("veth-att-br" "br-mesh" "veth-vic1-br" "veth-vic2-br" "veth-vic3-br")

if [ "$EUID" -ne 0 ]; then echo "ERROR: Must be root."; exit 1; fi

echo "Compiling $SRC_FILE -> $OBJ_FILE..."
clang -g -O2 -target bpf -c "$SRC_FILE" -o "$OBJ_FILE"

echo "Detaching existing filters..."
for dev in "${INTERFACES[@]}"; do tc qdisc del dev "$dev" clsact 2>/dev/null || true; done
rm -f /sys/fs/bpf/tc/globals/flow_map /sys/fs/bpf/tc/globals/lock_latency_hist /sys/fs/bpf/tc/globals/update_counter_map /sys/fs/bpf/tc/globals/tenant_qos_map /sys/fs/bpf/tc/globals/percpu_rate_limit_map /sys/fs/bpf/tc/globals/port_rate_limit_map /sys/fs/bpf/tc/globals/ip_stats /sys/fs/bpf/tc/globals/port_stats 2>/dev/null || true

echo "Attaching clsact qdiscs and eBPF filters for mode: $MODE..."
if [ "$MODE" = "qos_auto" ]; then
    # Tenant-facing bridge ports get ingress ACCOUNTING + enforcement (each packet counted once);
    # every other hook (port egress, br-mesh) gets the limiter only. See ebpf_qos_auto.c.
    for dev in "${INTERFACES[@]}"; do
        tc qdisc add dev "$dev" clsact
        if [ "$dev" = "br-mesh" ]; then
            tc filter add dev "$dev" ingress bpf da obj "$OBJ_FILE" sec classifier_limit
        else
            tc filter add dev "$dev" ingress bpf da obj "$OBJ_FILE" sec classifier_ingress
        fi
        tc filter add dev "$dev" egress bpf da obj "$OBJ_FILE" sec classifier_limit
        echo "  Attached to $dev"
    done
else
    for dev in "${INTERFACES[@]}"; do
        tc qdisc add dev "$dev" clsact
        tc filter add dev "$dev" ingress bpf da obj "$OBJ_FILE" sec classifier
        tc filter add dev "$dev" egress  bpf da obj "$OBJ_FILE" sec classifier
        echo "  Attached to $dev"
    done
fi

# Initialize BPF map entries for qos_tiered mode.
# Uses an exact 56-byte struct layout and verifies by read-back; fails loudly.
# Override the attacker cap with TIER_L2_RATE_BPS (bytes/s, default 5000000).
if [ "$MODE" = "qos_tiered" ]; then
    sleep 1
    python3 "$(dirname "$0")/seed_tiered_map.py" --l2-rate-bps "${TIER_L2_RATE_BPS:-5000000}"
fi
