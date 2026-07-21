#!/usr/bin/env bash
# stage1_manual_run.sh
# Performs a manual 10s sidecarless run with all collectors active.

IFACE="eno6"
RUN_DIR="results/stage1_manual_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RUN_DIR"

echo "=== Stage 1 Manual Run ==="
echo "Output Directory: $RUN_DIR"

# Ensure eBPF is compiled
clang -O2 -target bpf -c ebpf_mesh_router.c -o ebpf_mesh_router.o
if [ $? -ne 0 ]; then
    echo "eBPF compilation failed"
    exit 1
fi

# Attach eBPF
sudo ./attach_ebpf.sh
sleep 1

# Start Collectors
echo "Starting collectors..."
sudo ./collect_ebpf_stats.py "${RUN_DIR}/ebpf_lock_hist.jsonl" &
EBPF_PID=$!

# For cgroup stats, we need the victim cgroup path.
# We'll spawn the victims first.
sudo ./setup_topology.sh
sudo ./build_runc_bundles.sh
sudo ip netns exec ns_victim1 runc run -d -b victim_bundle_1 vic1_1 
sleep 2

# Find the cgroup for vic1_1 (it might be under /sys/fs/cgroup/vic1_1 depending on runc config)
CGROUP_PATH="/sys/fs/cgroup/vic1_1"
if [ ! -d "$CGROUP_PATH" ]; then
    # Fallback to searching
    CGROUP_PATH=$(find /sys/fs/cgroup -name "*vic1_1*" -type d | head -n 1)
fi

if [ -n "$CGROUP_PATH" ]; then
    sudo ./collect_cgroup_stats.py "$CGROUP_PATH" "${RUN_DIR}/cgroup_stats.csv" &
    CGROUP_PID=$!
else
    echo "Warning: Could not find vic1_1 cgroup path for stats."
fi

sudo bpftrace collect_bpftrace_lock.bt > "${RUN_DIR}/bpftrace_lock_wait.txt" 2>&1 &
BPFTRACE_PID=$!

# Pre-run network stats
sudo ./collect_network_stats.sh "$IFACE" "$RUN_DIR" "pre"

echo "Starting 10s flood..."
sudo ip netns exec ns_attacker hping3 -S -p 8080 -i u20 10.0.0.11 > /dev/null 2>&1 &
HPING_PID=$!

sudo ip netns exec ns_victim1 fortio load -c 10 -qps 50 -t 10s -json "${RUN_DIR}/fortio_raw.json" http://10.0.0.11:8080

echo "Stopping flood and collectors..."
sudo kill -9 $HPING_PID
sudo kill -INT $EBPF_PID
if [ -n "$CGROUP_PID" ]; then
    sudo kill -INT $CGROUP_PID
fi
sudo kill -INT $BPFTRACE_PID

# Post-run network stats
sudo ./collect_network_stats.sh "$IFACE" "$RUN_DIR" "post"

# Cleanup containers
sudo runc delete -f vic1_1 || true
sudo ./setup_topology.sh clean || true

echo "=== Run Complete ==="
echo "Please verify the files in $RUN_DIR:"
ls -lh "$RUN_DIR"
