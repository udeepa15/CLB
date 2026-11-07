#!/usr/bin/env bash
# attach_tc.sh police|tbf - kernel-native static rate-limit baselines (NO eBPF program attached).
#   police : ingress policer on each attacker's host-side veth (drops over-rate packets at the same
#            point where the eBPF arms enforce)
#   tbf    : token-bucket shaper on each attacker's own egress inside its namespace (queues, then
#            drops; the Kubernetes "bandwidth" CNI plugin style)
# Attackers come from the tenants file ($TENANTS_FILE, default tenants/default.json).
# Cap: TIER_L2_RATE_BPS bytes/s (default 5000000), identical to qos_tiered / qos_static, with the same
# burst rule as every other arm: max(rate/10, 128 KiB).
set -euo pipefail
MODE="${1:?usage: attach_tc.sh police|tbf}"
BRIDGE="${BRIDGE:-br-mesh}"
cd "$(dirname "$0")"
RATE_BPS="${TIER_L2_RATE_BPS:-5000000}"
BITS=$((RATE_BPS * 8))
BURST=$((RATE_BPS / 10)); [ "$BURST" -lt 131072 ] && BURST=131072

if [ "$EUID" -ne 0 ]; then echo "ERROR: Must be root."; exit 1; fi
mapfile -t ATTACKERS < <(python3 tenants.py attackers)          # "ns ns_veth br_veth ip"
mapfile -t PORTS < <(ip -o link show master "$BRIDGE" | awk -F': ' '{print $2}' | cut -d@ -f1)

# remove any previous eBPF/tc state so arms never leak into each other
for dev in "$BRIDGE" "${PORTS[@]}"; do
    tc qdisc del dev "$dev" clsact 2>/dev/null || true
    tc qdisc del dev "$dev" ingress 2>/dev/null || true
done
for a in "${ATTACKERS[@]}"; do
    read -r ns ns_veth br_veth ip <<< "$a"
    ip netns exec "$ns" tc qdisc del dev "$ns_veth" root 2>/dev/null || true
done
G=/sys/fs/bpf/tc/globals
for m in flow_map lock_latency_hist update_counter_map tenant_qos_map percpu_rate_limit_map port_rate_limit_map \
         ip_stats port_stats rate_cfg_map rate_state_map rate_cnt_map port_cfg_map port_state_map port_cnt_map; do
    rm -f "$G/$m"
done

for a in "${ATTACKERS[@]}"; do
    read -r ns ns_veth br_veth ip <<< "$a"
    case "$MODE" in
      police)
        tc qdisc add dev "$br_veth" handle ffff: ingress
        tc filter add dev "$br_veth" parent ffff: protocol ip prio 1 u32 \
            match ip src "$ip"/32 police rate "${BITS}bit" burst "$BURST" drop
        ;;
      tbf)
        ip netns exec "$ns" tc qdisc add dev "$ns_veth" root tbf rate "${BITS}bit" burst "$BURST" latency 50ms
        ;;
      *) echo "unknown mode $MODE"; exit 1 ;;
    esac
done
echo "attach_tc.sh: mode=$MODE attackers=${#ATTACKERS[@]} rate=${RATE_BPS}B/s burst=${BURST}B"
