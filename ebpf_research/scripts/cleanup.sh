#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

VICTIM_NS="victim_ns"
ATTACKER_NS="attacker_ns"
BRIDGE="br0"
VETH_V_HOST="veth_vic_h"
VETH_A_HOST="veth_att_h"

require_root() {
    if [[ "$(id -u)" -ne 0 ]]; then
        echo "[cleanup] Run as root: sudo ./scripts/cleanup.sh"
        exit 1
    fi
}

stop_containers() {
    runc delete -f victim_ct >/dev/null 2>&1 || true
    runc delete -f attacker_ct >/dev/null 2>&1 || true
}

cleanup_tc() {
    tc filter del dev "${VETH_V_HOST}" ingress >/dev/null 2>&1 || true
    tc filter del dev "${VETH_A_HOST}" ingress >/dev/null 2>&1 || true
    tc qdisc del dev "${VETH_V_HOST}" clsact >/dev/null 2>&1 || true
    tc qdisc del dev "${VETH_A_HOST}" clsact >/dev/null 2>&1 || true
    rm -f /sys/fs/bpf/ebpf_research/tc_shared_prog >/dev/null 2>&1 || true
    rm -rf /sys/fs/bpf/ebpf_research/shared >/dev/null 2>&1 || true
}

cleanup_network() {
    ip link del "${VETH_V_HOST}" >/dev/null 2>&1 || true
    ip link del "${VETH_A_HOST}" >/dev/null 2>&1 || true
    ip link set "${BRIDGE}" down >/dev/null 2>&1 || true
    ip link del "${BRIDGE}" type bridge >/dev/null 2>&1 || true
    ip netns del "${VICTIM_NS}" >/dev/null 2>&1 || true
    ip netns del "${ATTACKER_NS}" >/dev/null 2>&1 || true
}

main() {
    require_root
    stop_containers
    cleanup_tc
    cleanup_network
    echo "[cleanup] Environment teardown complete."
}

main "$@"
