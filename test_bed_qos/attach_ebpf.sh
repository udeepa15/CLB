#!/usr/bin/env bash
# attach_ebpf.sh: Flexible eBPF compiler & attacher for sidecarless, qos_tiered, and qos_dynamic.

set -euo pipefail

MODE="${1:-sidecarless}"

if [ "$MODE" = "qos_tiered" ]; then
    SRC_FILE="ebpf_qos_tiered.c"
    OBJ_FILE="ebpf_qos_tiered.o"
elif [ "$MODE" = "qos_dynamic" ]; then
    SRC_FILE="ebpf_qos_dynamic.c"
    OBJ_FILE="ebpf_qos_dynamic.o"
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
rm -f /sys/fs/bpf/tc/globals/flow_map /sys/fs/bpf/tc/globals/lock_latency_hist /sys/fs/bpf/tc/globals/update_counter_map /sys/fs/bpf/tc/globals/tenant_qos_map /sys/fs/bpf/tc/globals/percpu_rate_limit_map 2>/dev/null || true

echo "Attaching clsact qdiscs and eBPF filters for mode: $MODE..."
for dev in "${INTERFACES[@]}"; do
    tc qdisc add dev "$dev" clsact
    tc filter add dev "$dev" ingress bpf da obj "$OBJ_FILE" sec classifier
    tc filter add dev "$dev" egress  bpf da obj "$OBJ_FILE" sec classifier
    echo "  Attached to $dev"
done

# Initialize BPF map entries for qos_tiered mode
if [ "$MODE" = "qos_tiered" ]; then
    sleep 1
    MAP_PIN="/sys/fs/bpf/tc/globals/tenant_qos_map"
    if [ -e "$MAP_PIN" ]; then
        echo "Initializing tenant_qos_map entries via bpftool..."
        # Helper to convert IP to hex key
        # 10.0.0.10 -> hex 0x0a00000a (in network byte order: 10 + 0*256 + 0*65536 + 10*16777216)
        # Victims (10.0.0.10, 10.0.0.11, 10.0.0.12) -> Tier 0 (L0 Uncapped)
        bpftool map update pinned "$MAP_PIN" key 10 0 0 10 value 0 0 0 0  0 0 0 0 0 0 0 0  0 0 0 0 0 0 0 0  0 0 0 0 0 0 0 0  0 0 0 0 0 0 0 0  0 0 0 0 0 0 0 0  0 0 0 0 0 0 0 0 2>/dev/null || true
        bpftool map update pinned "$MAP_PIN" key 11 0 0 10 value 0 0 0 0  0 0 0 0 0 0 0 0  0 0 0 0 0 0 0 0  0 0 0 0 0 0 0 0  0 0 0 0 0 0 0 0  0 0 0 0 0 0 0 0  0 0 0 0 0 0 0 0 2>/dev/null || true
        bpftool map update pinned "$MAP_PIN" key 12 0 0 10 value 0 0 0 0  0 0 0 0 0 0 0 0  0 0 0 0 0 0 0 0  0 0 0 0 0 0 0 0  0 0 0 0 0 0 0 0  0 0 0 0 0 0 0 0  0 0 0 0 0 0 0 0 2>/dev/null || true
        # Attacker (10.0.0.20) -> Tier 2 (L2 Capped)
        bpftool map update pinned "$MAP_PIN" key 20 0 0 10 value 2 0 0 0  0 0 0 0 0 0 0 0  0 0 0 0 0 0 0 0  0 0 0 0 0 0 0 0  0 0 0 0 0 0 0 0  0 0 0 0 0 0 0 0  0 0 0 0 0 0 0 0 2>/dev/null || true
    fi
fi
