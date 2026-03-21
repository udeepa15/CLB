#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ALPINE_DIR="$ROOT_DIR/containers/alpine-rootfs"
TEMPLATE_ROOTFS="$ALPINE_DIR/rootfs-template"
RUNTIME_DIR="$ROOT_DIR/containers/runtime"
WORKLOADS_DIR="$ROOT_DIR/workloads"
CONFIGS_DIR="$ROOT_DIR/containers/configs"

ALPINE_VERSION="3.20.2"
ALPINE_ARCH="x86_64"
ALPINE_TAR="alpine-minirootfs-${ALPINE_VERSION}-${ALPINE_ARCH}.tar.gz"
ALPINE_URL="https://dl-cdn.alpinelinux.org/alpine/v3.20/releases/${ALPINE_ARCH}/${ALPINE_TAR}"
CACHE_TAR="/tmp/${ALPINE_TAR}"

need_root() {
  if [[ ${EUID} -ne 0 ]]; then
    echo "[setup-rootfs] Please run as root (sudo)."
    exit 1
  fi
}

prepare_template() {
  mkdir -p "$ALPINE_DIR"

  if [[ ! -f "$CACHE_TAR" ]]; then
    echo "[setup-rootfs] Downloading Alpine rootfs: $ALPINE_URL"
    curl -L "$ALPINE_URL" -o "$CACHE_TAR"
  else
    echo "[setup-rootfs] Using cached Alpine tarball: $CACHE_TAR"
  fi

  echo "[setup-rootfs] Recreating rootfs template: $TEMPLATE_ROOTFS"
  rm -rf "$TEMPLATE_ROOTFS"
  mkdir -p "$TEMPLATE_ROOTFS"
  tar -xzf "$CACHE_TAR" -C "$TEMPLATE_ROOTFS"

  cp /etc/resolv.conf "$TEMPLATE_ROOTFS/etc/resolv.conf"

  echo "[setup-rootfs] Installing packages inside template rootfs"
  chroot "$TEMPLATE_ROOTFS" /bin/sh -lc "apk update && apk add --no-cache python3 py3-pip curl iperf3"

  mkdir -p "$TEMPLATE_ROOTFS/usr/local/bin"
  cp "$WORKLOADS_DIR/victim.sh" "$TEMPLATE_ROOTFS/usr/local/bin/victim.sh"
  cp "$WORKLOADS_DIR/noisy.sh" "$TEMPLATE_ROOTFS/usr/local/bin/noisy.sh"
  chmod +x "$TEMPLATE_ROOTFS/usr/local/bin/victim.sh" "$TEMPLATE_ROOTFS/usr/local/bin/noisy.sh"
}

prepare_bundles() {
  mkdir -p "$RUNTIME_DIR"

  for name in tenant1 tenant2 noisy; do
    local bundle="$RUNTIME_DIR/$name"
    echo "[setup-rootfs] Preparing bundle: $bundle"
    rm -rf "$bundle"
    mkdir -p "$bundle"

    cp -a "$TEMPLATE_ROOTFS" "$bundle/rootfs"
    cp "$CONFIGS_DIR/$name.json" "$bundle/config.json"
  done
}

need_root
prepare_template
prepare_bundles

echo "[setup-rootfs] Done. Bundles are under: $RUNTIME_DIR"
