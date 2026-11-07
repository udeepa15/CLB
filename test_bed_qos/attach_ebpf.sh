#!/usr/bin/env bash
# attach_ebpf.sh MODE - compile one data plane and attach it to every hook of the testbed bridge.
#
#   sidecarless : ebpf_mesh_router.c (unprotected shared-key baseline): `classifier` on every hook
#   qos_tiered  : ebpf_qos_tiered.c   \  `classifier_ingress` on tenant ports (ingress direction),
#   qos_dynamic : ebpf_qos_dynamic.c   > so each packet is charged once, at its source;
#   qos_auto    : ebpf_qos_auto.c     /  `classifier_telemetry` on every other hook.
#
# Tenant ports are discovered from the bridge (all members of $BRIDGE), so this works for any number
# of tenants. All commands go through one `tc -batch` call (hundreds of hooks at 100 tenants).
# Env: BRIDGE (default br-mesh), TIER_L2_RATE_BPS (qos_tiered attacker cap, default 5000000),
#      MESH_UPDATES (sidecarless shared-key updates per packet, default 50).
set -euo pipefail

MODE="${1:-sidecarless}"
BRIDGE="${BRIDGE:-br-mesh}"
cd "$(dirname "$0")"

case "$MODE" in
    qos_tiered)  SRC=ebpf_qos_tiered.c ;;
    qos_dynamic) SRC=ebpf_qos_dynamic.c ;;
    qos_auto)    SRC=ebpf_qos_auto.c ;;
    sidecarless) SRC=ebpf_mesh_router.c ;;
    *) echo "ERROR: unknown mode '$MODE' (sidecarless|qos_tiered|qos_dynamic|qos_auto)"; exit 2 ;;
esac
OBJ="${SRC%.c}.o"

if [ "$EUID" -ne 0 ]; then echo "ERROR: Must be root."; exit 1; fi

mapfile -t PORTS < <(ip -o link show master "$BRIDGE" | awk -F': ' '{print $2}' | cut -d@ -f1)
if [ "${#PORTS[@]}" -eq 0 ]; then echo "ERROR: no ports on $BRIDGE (run setup_topology.sh first)"; exit 1; fi

echo "Compiling $SRC -> $OBJ..."
EXTRA=()
if [ "$MODE" = "sidecarless" ] && [ -n "${MESH_UPDATES:-}" ]; then EXTRA=(-DMESH_UPDATES="$MESH_UPDATES"); fi
clang -g -O2 -Wall -target bpf "${EXTRA[@]}" -c "$SRC" -o "$OBJ"

echo "Detaching existing filters and removing pinned maps..."
for dev in "$BRIDGE" "${PORTS[@]}"; do
    tc qdisc del dev "$dev" clsact 2>/dev/null || true
    tc qdisc del dev "$dev" ingress 2>/dev/null || true      # left by the tc police baseline
done
# the tc tbf baseline leaves a shaper inside each attacker namespace: remove it, or it keeps throttling
# the attacker in later trials of other arms when the topology is reused across trials
while read -r ns ns_veth _br _ip; do
    ip netns exec "$ns" tc qdisc del dev "$ns_veth" root 2>/dev/null || true
done < <(python3 tenants.py attackers)
G=/sys/fs/bpf/tc/globals
for m in flow_map lock_latency_hist update_counter_map tenant_qos_map \
         percpu_rate_limit_map port_rate_limit_map ip_stats port_stats \
         rate_cfg_map rate_state_map rate_cnt_map port_cfg_map port_state_map port_cnt_map; do
    rm -f "$G/$m"
done

BATCH=$(mktemp)
trap 'rm -f "$BATCH"' EXIT
for dev in "$BRIDGE" "${PORTS[@]}"; do
    echo "qdisc add dev $dev clsact" >> "$BATCH"
    if [ "$MODE" = "sidecarless" ]; then
        echo "filter add dev $dev ingress bpf da obj $OBJ sec classifier" >> "$BATCH"
        echo "filter add dev $dev egress bpf da obj $OBJ sec classifier" >> "$BATCH"
    elif [ "$dev" = "$BRIDGE" ]; then
        echo "filter add dev $dev ingress bpf da obj $OBJ sec classifier_telemetry" >> "$BATCH"
        echo "filter add dev $dev egress bpf da obj $OBJ sec classifier_telemetry" >> "$BATCH"
    else
        echo "filter add dev $dev ingress bpf da obj $OBJ sec classifier_ingress" >> "$BATCH"
        echo "filter add dev $dev egress bpf da obj $OBJ sec classifier_telemetry" >> "$BATCH"
    fi
done
echo "Attaching $MODE to $BRIDGE + ${#PORTS[@]} tenant ports..."
# In batch mode tc keeps every loaded object's fds open until it exits: ~10 fds per hook, i.e. ~2000 at
# 100 tenants, above the 1024 soft limit sudo gives. Raise the soft limit for this process only.
ulimit -n 65536 2>/dev/null || ulimit -n "$(ulimit -Hn)"
tc -batch "$BATCH"

# qos_tiered: seed tenant_qos_map with the exact 56-byte struct and verify by read-back (fails loudly).
if [ "$MODE" = "qos_tiered" ]; then
    python3 seed_tiered_map.py --l2-rate-bps "${TIER_L2_RATE_BPS:-5000000}"
fi
echo "Attached $MODE."
