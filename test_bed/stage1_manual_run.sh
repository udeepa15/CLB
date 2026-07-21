#!/usr/bin/env bash
# stage1_manual_run.sh
# Performs a manual 10s sidecarless run with all collectors active.

IFACE="eno6"
RUN_DIR="results/stage1_manual_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RUN_DIR"

echo "=== Stage 1 Manual Run ==="
echo "Output Directory: $RUN_DIR"

# Compile eBPF
clang -O2 -target bpf -c ebpf_mesh_router.c -o ebpf_mesh_router.o
if [ $? -ne 0 ]; then
    echo "eBPF compilation failed"
    exit 1
fi

# Attach eBPF
sudo ./attach_ebpf.sh
sleep 1

# Setup Topology & Base Bundles
sudo ./setup_topology.sh
sudo ./build_runc_bundles.sh

# Start Attacker Container
sudo runc kill attacker_container KILL 2>/dev/null || true; sudo runc delete attacker_container 2>/dev/null || true
sudo runc run --bundle attacker_bundle -d attacker_container

# Start Victim 1 Container
sudo runc kill "victim_container_1" KILL 2>/dev/null || true; sudo runc delete "victim_container_1" 2>/dev/null || true
rm -rf "victim_bundle_1"
cp -r victim_bundle "victim_bundle_1"
sed -i "s/ns_victim/ns_victim1/g" "victim_bundle_1/config.json"
sudo runc run --bundle "victim_bundle_1" -d "victim_container_1"
sleep 2

# Start Collectors
echo "Starting collectors..."
sudo ./collect_ebpf_stats.py "${RUN_DIR}/ebpf_lock_hist.jsonl" &
EBPF_PID=$!

CGROUP_PATH=$(find /sys/fs/cgroup -name "victim_container_1" -type d | head -n 1)
if [ -n "$CGROUP_PATH" ]; then
    sudo ./collect_cgroup_stats.py "$CGROUP_PATH" "${RUN_DIR}/cgroup_stats.csv" &
    CGROUP_PID=$!
else
    echo "Warning: Could not find victim_container_1 cgroup path for stats."
fi

sudo bpftrace collect_bpftrace_lock.bt > "${RUN_DIR}/bpftrace_lock_wait.txt" 2>&1 &
BPFTRACE_PID=$!

# Pre-run network stats
sudo ./collect_network_stats.sh "$IFACE" "$RUN_DIR" "pre"

echo "Starting 10s flood..."
sudo ip netns exec ns_attacker hping3 --udp -p 9999 -i u20 10.0.0.10 &>/dev/null &
HPING_PID=$!

# Wait a second for flood to stabilize
sleep 1

# Run Fortio against Victim 1 (10.0.0.10:80)
fortio load -c 10 -qps 50 -t 10s -json "${RUN_DIR}/fortio_raw.json" http://10.0.0.10:80/

echo "Stopping flood and collectors..."
sudo pkill -9 -f 'hping3'
sudo kill -INT $EBPF_PID
if [ -n "$CGROUP_PID" ]; then
    sudo kill -INT $CGROUP_PID
fi
sudo kill -INT $BPFTRACE_PID

# Post-run network stats
sudo ./collect_network_stats.sh "$IFACE" "$RUN_DIR" "post"

# Cleanup
sudo runc kill "victim_container_1" KILL 2>/dev/null || true; sudo runc delete "victim_container_1" 2>/dev/null || true
sudo runc kill attacker_container KILL 2>/dev/null || true; sudo runc delete attacker_container 2>/dev/null || true
sudo ./setup_topology.sh clean > /dev/null

echo "=== Run Complete ==="
echo "Please verify the files in $RUN_DIR:"
ls -lh "$RUN_DIR"
