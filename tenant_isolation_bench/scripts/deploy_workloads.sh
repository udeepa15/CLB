#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
STATE_DIR="${STATE_DIR:-/run/tenant_isolation_bench}"
LOG_DIR="${LOG_DIR:-${ROOT_DIR}/results/raw}"
HTTP_PORT="${HTTP_PORT:-8080}"
ADV_PORT="${ADV_PORT:-9090}"

NS_PREFIX="v_netns_"
HOST_IF_PREFIX="veth_v"
HOST_ADV_IF="veth_adv"
HOST_NET_OCTET="10.200"
ADV_NET_OCTET="7"

check_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    echo "[deploy] Run as root: sudo ./scripts/deploy_workloads.sh <start|stop|restart>"
    exit 1
  fi
}

require_bin() {
  local missing=0
  for bin in ip python3; do
    if ! command -v "${bin}" >/dev/null 2>&1; then
      echo "[deploy] Missing dependency: ${bin}"
      missing=1
    fi
  done
  if [[ "${missing}" -ne 0 ]]; then
    exit 1
  fi
}

ns_name() {
  local idx="$1"
  if [[ "${idx}" == "adv" ]]; then
    echo "${NS_PREFIX}adv"
  else
    echo "${NS_PREFIX}${idx}"
  fi
}

host_if_name() {
  local idx="$1"
  if [[ "${idx}" == "adv" ]]; then
    echo "${HOST_ADV_IF}"
  else
    echo "${HOST_IF_PREFIX}${idx}"
  fi
}

subnet_octet() {
  local idx="$1"
  if [[ "${idx}" == "adv" ]]; then
    echo "${ADV_NET_OCTET}"
  else
    echo "${idx}"
  fi
}

host_ip() {
  local idx="$1"
  echo "${HOST_NET_OCTET}.$(subnet_octet "${idx}").1/24"
}

ns_ip() {
  local idx="$1"
  echo "${HOST_NET_OCTET}.$(subnet_octet "${idx}").2/24"
}

ns_ip_cidr() {
  ns_ip "$1"
}

ns_gateway() {
  local idx="$1"
  echo "${HOST_NET_OCTET}.$(subnet_octet "${idx}").1"
}

server_label() {
  local idx="$1"
  if [[ "${idx}" == "adv" ]]; then
    echo "adversary"
  else
    echo "victim-${idx}"
  fi
}

pid_file() {
  local idx="$1"
  echo "${STATE_DIR}/$(ns_name "${idx}").pid"
}

log_file() {
  local idx="$1"
  echo "${STATE_DIR}/$(ns_name "${idx}").log"
}

ensure_namespace() {
  local ns="$1"
  if ! ip netns list | grep -q "^${ns}\b"; then
    ip netns add "${ns}"
  fi
}

ensure_link() {
  local idx="$1"
  local ns
  local host_if
  local host_address
  local tenant_address
  local ns_address

  ns="$(ns_name "${idx}")"
  host_if="$(host_if_name "${idx}")"
  host_address="$(host_ip "${idx}")"
  tenant_address="$(ns_ip_cidr "${idx}")"
  ns_address="$(ns_ip "${idx}")"

  ip link show "${host_if}" >/dev/null 2>&1 && ip link del "${host_if}" || true

  ip link add "${host_if}" type veth peer name eth0
  ip link set eth0 netns "${ns}"
  ip addr add "${host_address}" dev "${host_if}"
  ip link set "${host_if}" up

  ip -n "${ns}" link set lo up
  ip -n "${ns}" addr flush dev eth0 >/dev/null 2>&1 || true
  ip -n "${ns}" addr add "${tenant_address}" dev eth0
  ip -n "${ns}" link set eth0 up
  ip -n "${ns}" route replace default via "$(ns_gateway "${idx}")"

  printf '%s\n' "${ns_address}" >/dev/null
}

launch_server() {
  local idx="$1"
  local port="${2:-${HTTP_PORT}}"
  local bind_ip
  local ns
  local log
  local pidfile
  local body

  ns="$(ns_name "${idx}")"
  bind_ip="$(ns_ip "${idx}" | cut -d/ -f1)"
  log="$(log_file "${idx}")"
  pidfile="$(pid_file "${idx}")"
  body="$(server_label "${idx}")"

  mkdir -p "${STATE_DIR}" "${LOG_DIR}"
  : > "${log}"
  ip netns exec "${ns}" python3 "${SCRIPT_DIR}/http_echo_server.py" --bind "${bind_ip}" --port "${port}" --body "${body}" > "${log}" 2>&1 &
  echo $! > "${pidfile}"
}

wait_for_port() {
  local ip_addr="$1"
  local port="$2"
  local tries=0
  while [[ "${tries}" -lt 30 ]]; do
    if timeout 1 bash -c "cat < /dev/null > /dev/tcp/${ip_addr}/${port}" >/dev/null 2>&1; then
      return 0
    fi
    tries=$((tries + 1))
    sleep 1
  done
  echo "[deploy] Listener did not become ready on ${ip_addr}:${port}" >&2
  return 1
}

stop_server() {
  local idx="$1"
  local pidfile
  pidfile="$(pid_file "${idx}")"
  if [[ -f "${pidfile}" ]]; then
    local pid
    pid="$(cat "${pidfile}")"
    kill "${pid}" >/dev/null 2>&1 || true
    wait "${pid}" >/dev/null 2>&1 || true
    rm -f "${pidfile}"
  fi
}

start_all() {
  local idx
  mkdir -p "${STATE_DIR}" "${LOG_DIR}"
  sysctl -w net.ipv4.ip_forward=1 >/dev/null

  stop_all || true

  for idx in 1 2 3 4 5 6 adv; do
    ensure_namespace "$(ns_name "${idx}")"
    ensure_link "${idx}"
  done

  for idx in 1 2 3 4 5 6; do
    launch_server "${idx}"
    wait_for_port "$(ns_ip "${idx}" | cut -d/ -f1)" "${HTTP_PORT}"
  done

  launch_server adv "${ADV_PORT}"
  wait_for_port "$(ns_ip adv | cut -d/ -f1)" "${ADV_PORT}"

  echo "[deploy] Workloads are ready."
  for idx in 1 2 3 4 5 6; do
    echo "[deploy] $(ns_name "${idx}") -> http://$(ns_ip "${idx}" | cut -d/ -f1):${HTTP_PORT}/"
  done
  echo "[deploy] $(ns_name adv) -> http://$(ns_ip adv | cut -d/ -f1):${ADV_PORT}/"
}

stop_all() {
  local idx
  for idx in adv 1 2 3 4 5 6; do
    stop_server "${idx}"
  done

  for idx in adv 1 2 3 4 5 6; do
    local host_if
    local ns
    host_if="$(host_if_name "${idx}")"
    ns="$(ns_name "${idx}")"
    ip link del "${host_if}" >/dev/null 2>&1 || true
    ip netns del "${ns}" >/dev/null 2>&1 || true
  done

  rm -f "${STATE_DIR}"/*.pid >/dev/null 2>&1 || true
}

main() {
  check_root
  require_bin
  mkdir -p "${STATE_DIR}" "${LOG_DIR}"

  case "${1:-}" in
    start)
      start_all
      ;;
    stop)
      stop_all
      ;;
    restart)
      stop_all || true
      start_all
      ;;
    *)
      echo "Usage: $0 {start|stop|restart}"
      exit 1
      ;;
  esac
}

main "$@"
