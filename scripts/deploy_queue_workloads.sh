#!/usr/bin/env bash
set -euo pipefail
# deploy_queue_workloads.sh - create netns tenants and run queue workers in sidecar or eBPF mode
# Usage: deploy_queue_workloads.sh --num-tenants 3 --duration 60 --mode sidecar|sidecarless_ebpf

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$REPO_ROOT/ebpf_research"
RESULTS="$ROOT/results"
mkdir -p "$RESULTS"

NUM_TENANTS=3
DURATION=60
MODE="sidecarless_ebpf"
BROKER_IP=10.200.0.1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --num-tenants) NUM_TENANTS="$2"; shift 2;;
    --duration) DURATION="$2"; shift 2;;
    --mode) MODE="$2"; shift 2;;
    --sidecar) MODE="sidecar"; shift 1;;
    --broker-ip) BROKER_IP="$2"; shift 2;;
    *) echo "Unknown arg: $1"; exit 1;;
  esac
done

echo "Deploying $NUM_TENANTS tenant namespaces (duration ${DURATION}s) mode=${MODE}"

if [ "$MODE" = "sidecar" ]; then
  if ! command -v socat >/dev/null 2>&1; then
    echo "socat is required for sidecar mode" >&2
    exit 1
  fi
fi

if [ "$MODE" = "sidecarless_ebpf" ]; then
  if ! command -v clang >/dev/null 2>&1; then
    echo "clang is required to compile bpf_sockops.c" >&2
    exit 1
  fi
  if ! command -v bpftool >/dev/null 2>&1; then
    echo "bpftool is required to load sockmap programs" >&2
    exit 1
  fi

  SOCKOPS_SRC="$REPO_ROOT/bpf/bpf_sockops.c"
  SOCKOPS_OBJ="$(mktemp /tmp/redis_sockops.XXXXXX.o)"

  clang -O2 -g -target bpf -c "$SOCKOPS_SRC" -o "$SOCKOPS_OBJ"

  if ! mountpoint -q /sys/fs/cgroup; then
    sudo mount -t cgroup2 none /sys/fs/cgroup || true
  fi

  sudo rm -rf /sys/fs/bpf/redis_sockops || true
  sudo rm -f /sys/fs/bpf/redis_sock_map || true
  sudo mkdir -p /sys/fs/bpf/redis_sockops
  sudo bpftool prog loadall "$SOCKOPS_OBJ" /sys/fs/bpf/redis_sockops
  sudo bpftool cgroup attach /sys/fs/cgroup sock_ops pinned /sys/fs/bpf/redis_sockops/bpf_sockmap_ctrl
  sudo bpftool prog attach pinned /sys/fs/bpf/redis_sockops/bpf_redis_redirect msg_verdict pinned /sys/fs/bpf/redis_sockops/redis_sock_map
  rm -f "$SOCKOPS_OBJ"
fi

WORKER_BROKER_IP="$BROKER_IP"
if [ "$MODE" = "sidecar" ]; then
  WORKER_BROKER_IP="127.0.0.1"
fi

# ensure bridge exists and is up (should already be from manage_broker.sh)
sudo ip link set dev br-queue up 2>/dev/null || true

for i in $(seq 1 "$NUM_TENANTS"); do
  ns=tenant${i}
  if ! ip netns list | grep -qw "$ns"; then
    sudo ip netns add "$ns"
  fi
  # create veth pair attached to the bridge so all tenants can reach broker
  veth_host=v_${ns}
  veth_ns=${ns}_v
  if ! ip link show "$veth_host" >/dev/null 2>&1; then
    # create veth and move one end to namespace
    sudo ip link add "$veth_host" type veth peer name "$veth_ns"
    sudo ip link set "$veth_ns" netns "$ns"
    # bring up veth on host first, then attach to bridge
    sudo ip link set "$veth_host" up
    sudo ip link set "$veth_host" master br-queue
    # assign IPs on the 10.200.0.0/24 subnet so all can reach broker at 10.200.0.1
    sudo ip netns exec "$ns" ip addr add 10.200.0.$((100 + i))/24 dev "$veth_ns" || true
    sudo ip netns exec "$ns" ip link set "$veth_ns" up
  fi

  if [ "$MODE" = "sidecar" ]; then
    sudo ip netns exec "$ns" bash -c "nohup socat TCP-LISTEN:6379,fork,reuseaddr TCP:${BROKER_IP}:6379 > $RESULTS/socat_${ns}.log 2>&1 & echo \$! > $RESULTS/socat_${ns}.pid"
  fi

  sudo ip netns exec "$ns" bash -c "nohup python3 $SCRIPT_DIR/queue_worker.py --broker-ip $WORKER_BROKER_IP --queue-name tenant_queue_v${i} --duration-sec $DURATION > $RESULTS/worker_${ns}.log 2>&1 & echo \$! > $RESULTS/worker_${ns}.pid"
done

echo "Deployed tenants. Logs: $RESULTS/"
