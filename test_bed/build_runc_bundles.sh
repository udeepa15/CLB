#!/usr/bin/env bash
# build_runc_bundles.sh: Builds OCI runtime bundles for runc without a Docker daemon.
#
# Process & Isolation Setup:
# - Prepares 'victim_bundle' and 'attacker_bundle' folders containing OCI compliant configurations.
# - Downloads and extracts a minimal Alpine Linux rootfs.
# - Victim: Installs python3 to run a basic HTTP server.
# - Attacker: Installs compilation tools, builds 'wrk2' from git, installs it, and trims the dev packages.
# - Configures cpu-pinning (cpuset) using cgroups v2:
#     - Victim container is pinned to CPU core 1.
#     - Attacker container is pinned to CPU core 1 (SAME core as Victim).
#     - Co-pinning forces the scheduler to interleave network softirqs on a single
#       core, inducing cross-traffic scheduling jitter on the victim data plane.
# - Hooks the container processes to the pre-created namespaces (/var/run/netns/ns_victim and ns_attacker)
#   by configuring the linux namespaces array in config.json.

set -euo pipefail

VICTIM_DIR="victim_bundle"
ATTACKER_DIR="attacker_bundle"
ROOTFS_TAR="alpine-minirootfs.tar.gz"
ALPINE_URL="https://dl-cdn.alpinelinux.org/alpine/v3.18/releases/x86_64/alpine-minirootfs-3.18.4-x86_64.tar.gz"

# Ensure script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run as root to perform chroot actions and mount filesystems." >&2
    exit 1
fi

# Ensure dependencies are installed on host
for cmd in wget tar jq runc; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: Required host tool '$cmd' is not installed." >&2
        exit 1
    fi
done

# Ensure target bundle directories exist
mkdir -p "$VICTIM_DIR/rootfs" "$ATTACKER_DIR/rootfs"

# Download alpine rootfs if not present
if [ ! -f "$ROOTFS_TAR" ]; then
    echo "Downloading Alpine minirootfs..."
    wget -qO "$ROOTFS_TAR" "$ALPINE_URL"
fi

# Extract rootfs if not already done
if [ ! -f "$VICTIM_DIR/rootfs/etc/alpine-release" ]; then
    echo "Extracting Alpine rootfs to Victim bundle..."
    tar -xzf "$ROOTFS_TAR" -C "$VICTIM_DIR/rootfs"
fi

if [ ! -f "$ATTACKER_DIR/rootfs/etc/alpine-release" ]; then
    echo "Extracting Alpine rootfs to Attacker bundle..."
    tar -xzf "$ROOTFS_TAR" -C "$ATTACKER_DIR/rootfs"
fi

# Setup resolv.conf inside rootfs to enable package downloads
cp /etc/resolv.conf "$VICTIM_DIR/rootfs/etc/resolv.conf"
cp /etc/resolv.conf "$ATTACKER_DIR/rootfs/etc/resolv.conf"

echo "Setting up Victim rootfs (Python HTTP server)..."
if [ ! -f "$VICTIM_DIR/rootfs/usr/bin/python3" ]; then
    # Mount proc/dev inside rootfs to allow apk to run without warnings
    mount -t proc proc "$VICTIM_DIR/rootfs/proc"
    mount --bind /dev "$VICTIM_DIR/rootfs/dev"
    trap 'umount "$VICTIM_DIR/rootfs/proc" 2>/dev/null || true; umount "$VICTIM_DIR/rootfs/dev" 2>/dev/null || true' EXIT

    chroot "$VICTIM_DIR/rootfs" apk update
    chroot "$VICTIM_DIR/rootfs" apk add --no-cache python3

    # Unmount victim mounts
    umount "$VICTIM_DIR/rootfs/proc"
    umount "$VICTIM_DIR/rootfs/dev"
    trap - EXIT
else
    echo "Python3 already installed inside Victim rootfs. Skipping..."
fi

echo "Setting up Attacker rootfs (wrk2 load generator)..."
if [ ! -f "$ATTACKER_DIR/rootfs/usr/bin/wrk2" ]; then
    # Mount proc/dev inside attacker rootfs
    mount -t proc proc "$ATTACKER_DIR/rootfs/proc"
    mount --bind /dev "$ATTACKER_DIR/rootfs/dev"
    trap 'umount "$ATTACKER_DIR/rootfs/proc" 2>/dev/null || true; umount "$ATTACKER_DIR/rootfs/dev" 2>/dev/null || true' EXIT

    chroot "$ATTACKER_DIR/rootfs" apk update
    chroot "$ATTACKER_DIR/rootfs" apk add --no-cache build-base git zlib-dev openssl-dev

    echo "Cloning and building wrk2..."
    chroot "$ATTACKER_DIR/rootfs" git clone https://github.com/giltene/wrk2.git /usr/src/wrk2
    chroot "$ATTACKER_DIR/rootfs" make -C /usr/src/wrk2 -j$(nproc)
    chroot "$ATTACKER_DIR/rootfs" cp /usr/src/wrk2/wrk /usr/bin/wrk2

    # Clean up build dependencies to minimize rootfs size
    chroot "$ATTACKER_DIR/rootfs" apk del build-base git
    chroot "$ATTACKER_DIR/rootfs" rm -rf /usr/src/wrk2

    # Unmount attacker mounts
    umount "$ATTACKER_DIR/rootfs/proc"
    umount "$ATTACKER_DIR/rootfs/dev"
    trap - EXIT
else
    echo "wrk2 already built inside Attacker rootfs. Skipping..."
fi

echo "Generating and configuring OCI config.json for Victim..."
cd "$VICTIM_DIR"
# Remove existing config.json to allow runc spec to run cleanly
rm -f config.json
runc spec
# Modify config.json using jq:
# - Run python3 HTTP server on port 80
# - Disable terminal allocations for background run
# - Restrict container to CPU 1 via cpuset cgroups v2 (same core as attacker)
# - Force join ns_victim netns
jq '.process.args = ["sh", "-c", "exec python3 -m http.server 80 >/dev/null 2>&1"] |
    .process.user.uid = 0 |
    .process.terminal = false |
    .linux.resources.cpu.cpus = "1" |
    (.linux.namespaces[] | select(.type == "network")) |= . + {"path": "/var/run/netns/ns_victim"}' config.json > config.json.tmp
mv config.json.tmp config.json
cd ..

echo "Generating and configuring OCI config.json for Attacker..."
cd "$ATTACKER_DIR"
# Remove existing config.json to allow runc spec to run cleanly
rm -f config.json
runc spec
# Modify config.json using jq:
# - Run a sleep infinity loop to keep the container daemon alive
# - Disable terminal allocations
# - Restrict container to CPU 1 via cpuset cgroups v2 (SAME core as victim)
#   This co-pinning is intentional: it forces the kernel scheduler to interleave
#   softirq / packet-processing work on one core, producing scheduling jitter.
# - Force join ns_attacker netns
jq '.process.args = ["sleep", "infinity"] |
    .process.user.uid = 0 |
    .process.terminal = false |
    .linux.resources.cpu.cpus = "1" |
    (.linux.namespaces[] | select(.type == "network")) |= . + {"path": "/var/run/netns/ns_attacker"}' config.json > config.json.tmp
mv config.json.tmp config.json
cd ..

echo "Runc bundles configuration successfully built."
