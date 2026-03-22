#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IFACE="${IFACE:-clb-br0}"
ACTION="${1:-attach}"
STRATEGY="${2:-dropper}"
OBJ="$SCRIPT_DIR/${STRATEGY}.o"

if [[ ${EUID} -ne 0 ]]; then
  echo "[ebpf-attach] Please run as root (sudo)."
  exit 1
fi

case "$ACTION" in
  attach)
    [[ -f "$OBJ" ]] || "$SCRIPT_DIR/build.sh" "$STRATEGY"
    echo "[ebpf-attach] Attaching tc/eBPF strategy=$STRATEGY to ingress on $IFACE"
    tc qdisc replace dev "$IFACE" clsact
    tc filter replace dev "$IFACE" ingress prio 1 handle 1 bpf da obj "$OBJ" sec classifier
    tc filter show dev "$IFACE" ingress
    ;;
  detach)
    echo "[ebpf-attach] Detaching tc/eBPF from $IFACE"
    tc filter del dev "$IFACE" ingress prio 1 handle 1 bpf 2>/dev/null || true
    tc qdisc del dev "$IFACE" clsact 2>/dev/null || true
    ;;
  status)
    tc qdisc show dev "$IFACE"
    tc -s filter show dev "$IFACE" ingress
    ;;
  drops)
    if [[ "$STRATEGY" == "adaptive" ]]; then
      python3 "$SCRIPT_DIR/../../core/bpf_map_ctl.py" stats 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("dropped",0))' 2>/dev/null || echo 0
    else
      tc -s filter show dev "$IFACE" ingress 2>/dev/null | awk '/dropped/ {for (i=1;i<=NF;i++) if ($i=="dropped") {print $(i+1); exit}} END {if (NR==0) print 0}'
    fi
    ;;
  *)
    echo "Usage: $0 [attach|detach|status|drops] [dropper|rate_limit|priority|adaptive]"
    exit 1
    ;;
esac
