#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OBJ="$SCRIPT_DIR/limiter.o"
IFACE="${IFACE:-clb-br0}"
ACTION="${1:-attach}"

if [[ ${EUID} -ne 0 ]]; then
  echo "[ebpf-attach] Please run as root (sudo)."
  exit 1
fi

case "$ACTION" in
  attach)
    [[ -f "$OBJ" ]] || "$SCRIPT_DIR/build.sh"
    echo "[ebpf-attach] Attaching tc/eBPF to ingress on $IFACE"
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
    tc filter show dev "$IFACE" ingress
    ;;
  *)
    echo "Usage: $0 [attach|detach|status]"
    exit 1
    ;;
esac
