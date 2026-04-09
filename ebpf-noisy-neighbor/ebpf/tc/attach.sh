#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IFACE="${IFACE:-clb-br0}"
ACTION="${1:-attach}"
STRATEGY="${2:-dropper}"
HOOK_POINT="${3:-${HOOK_POINT:-tc}}"
OBJ="$SCRIPT_DIR/${STRATEGY}.o"

if [[ ${EUID} -ne 0 ]]; then
  echo "[ebpf-attach] Please run as root (sudo)."
  exit 1
fi

case "$ACTION" in
  attach)
    [[ -f "$OBJ" ]] || "$SCRIPT_DIR/build.sh" "$STRATEGY"
    if [[ "$HOOK_POINT" == "xdp" ]]; then
      if [[ "$STRATEGY" != "dropper" && "$STRATEGY" != "rate_limit" ]]; then
        echo "[ebpf-attach] Strategy '$STRATEGY' has no XDP section; falling back to tc hook"
        HOOK_POINT="tc"
      fi
    fi
    if [[ "$HOOK_POINT" == "xdp" ]]; then
      echo "[ebpf-attach] Attaching XDP/eBPF strategy=$STRATEGY on $IFACE"
      ip link set dev "$IFACE" xdp off 2>/dev/null || true
      ip link set dev "$IFACE" xdp obj "$OBJ" sec xdp
      ip -details link show dev "$IFACE" | sed -n '/prog\/xdp/p' || true
    else
      echo "[ebpf-attach] Attaching tc/eBPF strategy=$STRATEGY to ingress on $IFACE"
      tc qdisc replace dev "$IFACE" clsact
      tc filter replace dev "$IFACE" ingress prio 1 handle 1 bpf da obj "$OBJ" sec classifier
      tc filter show dev "$IFACE" ingress
    fi
    ;;
  detach)
    echo "[ebpf-attach] Detaching eBPF from $IFACE (hook=$HOOK_POINT)"
    if [[ "$HOOK_POINT" == "xdp" ]]; then
      ip link set dev "$IFACE" xdp off 2>/dev/null || true
    elif [[ "$HOOK_POINT" == "tc" ]]; then
      tc filter del dev "$IFACE" ingress prio 1 handle 1 bpf 2>/dev/null || true
      tc qdisc del dev "$IFACE" clsact 2>/dev/null || true
    else
      ip link set dev "$IFACE" xdp off 2>/dev/null || true
      tc filter del dev "$IFACE" ingress prio 1 handle 1 bpf 2>/dev/null || true
      tc qdisc del dev "$IFACE" clsact 2>/dev/null || true
    fi
    ;;
  status)
    tc qdisc show dev "$IFACE"
    tc -s filter show dev "$IFACE" ingress
    ip -details link show dev "$IFACE" | sed -n '/prog\/xdp/p' || true
    ;;
  drops)
    read_drop_from_map() {
      local map_name="$1"
      bpftool -j map lookup name "$map_name" key hex 00 00 00 00 2>/dev/null | \
        python3 -c 'import json,sys,struct
o=sys.stdin.read().strip()
if not o:
    print(0); raise SystemExit(0)
try:
    v=json.loads(o).get("value",[])
    raw=bytes(int(x,16) for x in v)
    dropped=struct.unpack("<Q", raw[8:16])[0] if len(raw)>=16 else 0
    print(dropped)
except Exception:
    print(0)'
    }

    if [[ "$STRATEGY" == "adaptive" ]]; then
      python3 "$SCRIPT_DIR/../../core/bpf_map_ctl.py" stats 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("dropped",0))' 2>/dev/null || echo 0
    elif [[ "$STRATEGY" == "dropper" ]]; then
      read_drop_from_map "dropper_stats_map" 2>/dev/null || echo 0
    elif [[ "$STRATEGY" == "rate_limit" ]]; then
      read_drop_from_map "rate_limit_stats_map" 2>/dev/null || echo 0
    else
      tc -s filter show dev "$IFACE" ingress 2>/dev/null | awk '/dropped/ {for (i=1;i<=NF;i++) if ($i=="dropped") {print $(i+1); exit}} END {if (NR==0) print 0}'
    fi
    ;;
  *)
    echo "Usage: $0 [attach|detach|status|drops] [dropper|rate_limit|priority|adaptive] [tc|xdp|auto]"
    exit 1
    ;;
esac
