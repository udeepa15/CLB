#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSCTL_FILE="$ROOT_DIR/environments/sysctl.conf"

echo "[base-setup] Installing dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  ca-certificates \
  curl \
  jq \
  iproute2 \
  bpftool \
  iputils-ping \
  runc \
  clang \
  llvm \
  make \
  python3 \
  python3-pip \
  python3-yaml \
  python3-matplotlib \
  stress-ng \
  iperf3

echo "[base-setup] Applying sysctl config from $SYSCTL_FILE"
cp "$SYSCTL_FILE" /etc/sysctl.d/99-ebpf-noisy-neighbor.conf
sysctl --system >/dev/null

echo "[base-setup] Done."
