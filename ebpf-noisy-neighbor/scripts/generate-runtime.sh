#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE_ROOTFS="$ROOT_DIR/containers/alpine-rootfs/rootfs-template"
RUNTIME_DIR="$ROOT_DIR/containers/runtime"
TEMPLATE_TENANT="$ROOT_DIR/containers/configs/tenant1.json"
TEMPLATE_NOISY="$ROOT_DIR/containers/configs/noisy.json"

TOTAL_CONTAINERS="${1:-3}"
NOISE_LEVEL="${2:-medium}"

if [[ ${EUID} -ne 0 ]]; then
  echo "[generate-runtime] Please run as root (sudo)."
  exit 1
fi

if [[ "$TOTAL_CONTAINERS" -lt 3 ]]; then
  echo "[generate-runtime] containers must be >= 3"
  exit 1
fi

if [[ ! -d "$TEMPLATE_ROOTFS" ]]; then
  echo "[generate-runtime] rootfs template missing. Running setup-rootfs.sh"
  "$ROOT_DIR/containers/setup-rootfs.sh"
fi

mkdir -p "$RUNTIME_DIR"
rm -rf "$RUNTIME_DIR"/*

tenants=(tenant1 tenant2)
for ((i=3; i<=TOTAL_CONTAINERS-1; i++)); do
  tenants+=("tenant${i}")
done

# Keep noisy at 10.0.0.4 for stable eBPF filtering semantics.
declare -A ip_map=()
ip_map[tenant1]="10.0.0.2"
ip_map[tenant2]="10.0.0.3"
ip_map[noisy]="10.0.0.4"

next_octet=5
for t in "${tenants[@]}"; do
  if [[ "$t" == "tenant1" || "$t" == "tenant2" ]]; then
    continue
  fi
  ip_map[$t]="10.0.0.${next_octet}"
  next_octet=$((next_octet + 1))
done

noisy_targets=""
for t in "${tenants[@]}"; do
  ip="${ip_map[$t]}"
  if [[ -z "$noisy_targets" ]]; then
    noisy_targets="${ip}:8080"
  else
    noisy_targets=",${noisy_targets}"; noisy_targets="${noisy_targets#,}${ip}:8080"
  fi
done
# Fix accidental reverse concat by reconstructing deterministically.
noisy_targets=""
for t in "${tenants[@]}"; do
  ip="${ip_map[$t]}"
  noisy_targets+="${ip}:8080,"
done
noisy_targets="${noisy_targets%,}"

build_bundle() {
  local name="$1"
  local role="$2"
  local bundle="$RUNTIME_DIR/$name"

  echo "[generate-runtime] Preparing bundle for $name ($role)"
  mkdir -p "$bundle"
  cp -a "$TEMPLATE_ROOTFS" "$bundle/rootfs"

  if [[ "$role" == "tenant" ]]; then
    jq \
      --arg host "$name" \
      --arg tenant "$name" \
      ' .hostname = $host
      | .process.env = (.process.env
          | map(if startswith("TENANT_NAME=") then "TENANT_NAME=" + $tenant else . end)
        )
      ' "$TEMPLATE_TENANT" > "$bundle/config.json"
  else
    jq \
      --arg host "$name" \
      --arg targets "$noisy_targets" \
      --arg level "$NOISE_LEVEL" \
      ' .hostname = $host
      | .process.env = (.process.env
          | map(
              if startswith("NOISY_TARGETS=") then "NOISY_TARGETS=" + $targets
              elif startswith("NOISE_LEVEL=") then "NOISE_LEVEL=" + $level
              else . end
            )
        )
      ' "$TEMPLATE_NOISY" > "$bundle/config.json"
  fi
}

# Write inventory
inventory="$RUNTIME_DIR/inventory.csv"
echo "name,role,ip" > "$inventory"

for t in "${tenants[@]}"; do
  build_bundle "$t" "tenant"
  echo "$t,tenant,${ip_map[$t]}" >> "$inventory"
done

build_bundle noisy noisy
echo "noisy,noisy,${ip_map[noisy]}" >> "$inventory"

echo "[generate-runtime] Created runtime inventory: $inventory"
cat "$inventory"
