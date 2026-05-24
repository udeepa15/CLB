#!/usr/bin/env bash
set -euo pipefail
# deploy_queue_workloads.sh - create netns tenants, optionally run sidecar worker or in-namespace worker
# Usage: deploy_queue_workloads.sh --num-tenants 3 --duration 60 --sidecar (optional)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$REPO_ROOT/ebpf_research"
RESULTS="$ROOT/results"
mkdir -p "$RESULTS"

NUM_TENANTS=3
DURATION=60
SIDECAR=0
BROKER_IP=10.200.0.1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --num-tenants) NUM_TENANTS="$2"; shift 2;;
    --duration) DURATION="$2"; shift 2;;
    --sidecar) SIDECAR=1; shift 1;;
    --broker-ip) BROKER_IP="$2"; shift 2;;
    *) echo "Unknown arg: $1"; exit 1;;
  esac
done

echo "Deploying $NUM_TENANTS tenant namespaces (duration ${DURATION}s) sidecar=${SIDECAR}"

for i in $(seq 1 "$NUM_TENANTS"); do
  ns=tenant${i}
  if ! ip netns list | grep -qw "$ns"; then
    sudo ip netns add "$ns"
  fi
  # create veth pair to bridge if not exists
  veth_host=v_${ns}
  veth_ns=${ns}_v
  if ! ip link show "$veth_host" >/dev/null 2>&1; then
    sudo ip link add "$veth_host" type veth peer name "$veth_ns"
    sudo ip link set "$veth_ns" netns "$ns"
    sudo ip addr add 10.200.${i}.2/24 dev "$veth_host" || true
    sudo ip link set "$veth_host" up
    sudo ip netns exec "$ns" ip addr add 10.200.${i}.3/24 dev "$veth_ns" || true
    sudo ip netns exec "$ns" ip link set "$veth_ns" up
  fi

  # start worker inside namespace or leave for sidecar
  if [ "$SIDECAR" -eq 0 ]; then
    sudo ip netns exec "$ns" bash -c "nohup python3 $SCRIPT_DIR/queue_worker.py --broker-ip $BROKER_IP --queue-name tenant_queue_v${i} --duration-sec $DURATION > $RESULTS/worker_${ns}.log 2>&1 &"
  else
    echo "Sidecar mode: not starting in-namespace worker for $ns"
  fi
done

echo "Deployed tenants. Logs: $RESULTS/"
