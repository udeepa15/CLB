#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_ROOT="$(cd "${ROOT_DIR}/.." && pwd)"
BPF_DIR="${ROOT_DIR}/bpf"
BUNDLES_DIR="${ROOT_DIR}/bundles"
RESULTS_DIR="${ROOT_DIR}/results"

VICTIM_NS="victim_ns"
ATTACKER_NS="attacker_ns"
BRIDGE="br0"
VETH_V_HOST="veth_vic_h"
VETH_V_NS="veth_vic_n"
VETH_A_HOST="veth_att_h"
VETH_A_NS="veth_att_n"

VICTIM_IP="10.200.0.2/24"
ATTACKER_IP="10.200.0.3/24"
BRIDGE_IP="10.200.0.1/24"

ROOTFS_SOURCE="${PROJECT_ROOT}/ebpf-noisy-neighbor/containers/alpine-rootfs/rootfs-template"

require_root() {
    if [[ "$(id -u)" -ne 0 ]]; then
        echo "[setup] Run as root: sudo ./scripts/setup.sh"
        exit 1
    fi
}

require_bin() {
    local missing=0
    for bin in ip tc runc jq clang llc bpftool; do
        if ! command -v "${bin}" >/dev/null 2>&1; then
            echo "[setup] Missing dependency: ${bin}"
            missing=1
        fi
    done
    if [[ "${missing}" -ne 0 ]]; then
        exit 1
    fi
}

setup_bpffs() {
    if ! mountpoint -q /sys/fs/bpf; then
        mount -t bpf bpf /sys/fs/bpf
    fi
    mkdir -p /sys/fs/bpf/ebpf_research
}

setup_network() {
    ip link show "${BRIDGE}" >/dev/null 2>&1 || ip link add name "${BRIDGE}" type bridge
    ip addr show dev "${BRIDGE}" | grep -q "10.200.0.1/24" || ip addr add "${BRIDGE_IP}" dev "${BRIDGE}"
    ip link set "${BRIDGE}" up

    ip netns list | grep -q "^${VICTIM_NS}\b" || ip netns add "${VICTIM_NS}"
    ip netns list | grep -q "^${ATTACKER_NS}\b" || ip netns add "${ATTACKER_NS}"

    ip link show "${VETH_V_HOST}" >/dev/null 2>&1 || ip link add "${VETH_V_HOST}" type veth peer name "${VETH_V_NS}"
    ip link show "${VETH_A_HOST}" >/dev/null 2>&1 || ip link add "${VETH_A_HOST}" type veth peer name "${VETH_A_NS}"

    ip link set "${VETH_V_HOST}" master "${BRIDGE}"
    ip link set "${VETH_A_HOST}" master "${BRIDGE}"
    ip link set "${VETH_V_HOST}" up
    ip link set "${VETH_A_HOST}" up

    if ip link show "${VETH_V_NS}" >/dev/null 2>&1; then
        ip link set "${VETH_V_NS}" netns "${VICTIM_NS}"
    fi
    if ip link show "${VETH_A_NS}" >/dev/null 2>&1; then
        ip link set "${VETH_A_NS}" netns "${ATTACKER_NS}"
    fi

    ip -n "${VICTIM_NS}" addr flush dev "${VETH_V_NS}" || true
    ip -n "${ATTACKER_NS}" addr flush dev "${VETH_A_NS}" || true
    ip -n "${VICTIM_NS}" addr add "${VICTIM_IP}" dev "${VETH_V_NS}" || true
    ip -n "${ATTACKER_NS}" addr add "${ATTACKER_IP}" dev "${VETH_A_NS}" || true
    ip -n "${VICTIM_NS}" link set lo up
    ip -n "${ATTACKER_NS}" link set lo up
    ip -n "${VICTIM_NS}" link set "${VETH_V_NS}" up
    ip -n "${ATTACKER_NS}" link set "${VETH_A_NS}" up
    ip -n "${VICTIM_NS}" route replace default via 10.200.0.1
    ip -n "${ATTACKER_NS}" route replace default via 10.200.0.1
}

prepare_rootfs() {
    local name="$1"
    local rootfs="${BUNDLES_DIR}/${name}/rootfs"
    local initialized_marker="${rootfs}/.rootfs_initialized"

    mkdir -p "${rootfs}"
    if [[ ! -f "${initialized_marker}" ]]; then
        if [[ -x "${rootfs}/bin/busybox" ]]; then
            touch "${initialized_marker}"
        fi
    fi

    if [[ ! -f "${initialized_marker}" ]]; then
        if [[ ! -d "${ROOTFS_SOURCE}" ]]; then
            echo "[setup] Missing rootfs template: ${ROOTFS_SOURCE}"
            exit 1
        fi
        cp -a "${ROOTFS_SOURCE}/." "${rootfs}/"
        touch "${initialized_marker}"
    fi

    mkdir -p "${rootfs}/www"
    cat > "${rootfs}/www/index.html" <<'EOF'
noisy-neighbor benchmark endpoint
EOF

    cat > "${rootfs}/bin/http-echo.sh" <<'EOF'
#!/bin/sh
printf 'HTTP/1.1 200 OK\r\n'
printf 'Content-Type: text/plain\r\n'
printf 'Content-Length: 3\r\n'
printf 'Connection: close\r\n\r\n'
printf 'ok\n'
EOF
    chmod +x "${rootfs}/bin/http-echo.sh"
}

write_bundle_config() {
    local name="$1"
    local ns_name="$2"
    local process_cmd="$3"
    local bundle="${BUNDLES_DIR}/${name}"

    mkdir -p "${bundle}"
    prepare_rootfs "${name}"

    (
        cd "${bundle}"
        if [[ ! -f config.json ]]; then
            runc spec
        fi
    )

    jq \
      --arg host "${name}" \
      --arg netns "/var/run/netns/${ns_name}" \
      --arg cmd "${process_cmd}" \
      '
        .hostname = $host
        | .root.path = "rootfs"
        | .root.readonly = false
        | .process.terminal = false
        | .process.args = ["/bin/sh", "-c", $cmd]
        | .linux.namespaces = (.linux.namespaces | map(
            if .type == "network" then . + {"path": $netns} else . end
          ))
      ' "${bundle}/config.json" > "${bundle}/config.json.tmp"

    mv "${bundle}/config.json.tmp" "${bundle}/config.json"
}

build_bpf() {
    clang -O2 -g -target bpf -D__TARGET_ARCH_x86 \
        -c "${BPF_DIR}/counter_tc.c" -o "${BPF_DIR}/counter_tc_shared.o"

    clang -O2 -g -target bpf -D__TARGET_ARCH_x86 -DPER_IFINDEX_KEY=1 \
        -c "${BPF_DIR}/counter_tc.c" -o "${BPF_DIR}/counter_tc_isolated.o"
}

main() {
    require_root
    require_bin

    mkdir -p "${BUNDLES_DIR}" "${RESULTS_DIR}"

    setup_bpffs
    setup_network

    write_bundle_config "victim" "${VICTIM_NS}" "busybox nc -lk -p 8080 -e /bin/http-echo.sh"
    write_bundle_config "attacker" "${ATTACKER_NS}" "sleep infinity"

    build_bpf

    echo "[setup] Completed."
    echo "[setup] Victim endpoint: http://10.200.0.2:8080/"
}

main "$@"
