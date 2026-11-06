#!/usr/bin/env bash
# attach_tc.sh: kernel-native static rate-limit baselines (NO eBPF program attached).
#   police : ingress policer on the attacker's host-side veth (drops over-rate packets at the
#            same point where the eBPF arms enforce)
#   tbf    : token-bucket shaper on the attacker's own egress inside ns_attacker (queues, then
#            drops; the Kubernetes "bandwidth" CNI plugin style)
# Cap: TIER_L2_RATE_BPS bytes/s (default 5000000), identical to qos_tiered / qos_percpu_static.
set -euo pipefail
MODE="${1:?usage: attach_tc.sh police|tbf}"
RATE_BPS="${TIER_L2_RATE_BPS:-5000000}"
BITS=$((RATE_BPS * 8))
BURST=$((RATE_BPS / 10)); [ "$BURST" -lt 65536 ] && BURST=65536
ATT_IP="10.0.0.20"
INTERFACES=("veth-att-br" "br-mesh" "veth-vic1-br" "veth-vic2-br" "veth-vic3-br")

if [ "$EUID" -ne 0 ]; then echo "ERROR: Must be root."; exit 1; fi

# remove any previous eBPF/tc state so arms never leak into each other
for dev in "${INTERFACES[@]}"; do
    tc qdisc del dev "$dev" clsact 2>/dev/null || true
    tc qdisc del dev "$dev" ingress 2>/dev/null || true
done
ip netns exec ns_attacker tc qdisc del dev veth-attacker root 2>/dev/null || true
rm -f /sys/fs/bpf/tc/globals/{flow_map,lock_latency_hist,update_counter_map,tenant_qos_map,percpu_rate_limit_map} 2>/dev/null || true

case "$MODE" in
  police)
    tc qdisc add dev veth-att-br handle ffff: ingress
    tc filter add dev veth-att-br parent ffff: protocol ip prio 1 u32 \
        match ip src "$ATT_IP"/32 police rate "${BITS}bit" burst "$BURST" drop
    ;;
  tbf)
    ip netns exec ns_attacker tc qdisc add dev veth-attacker root tbf \
        rate "${BITS}bit" burst "$BURST" latency 50ms
    ;;
  *) echo "unknown mode $MODE"; exit 1 ;;
esac
echo "attach_tc.sh: mode=$MODE rate=${RATE_BPS}B/s burst=${BURST}B"
