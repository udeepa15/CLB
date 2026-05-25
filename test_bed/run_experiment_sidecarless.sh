#!/usr/bin/env bash
# run_experiment_sidecarless.sh: Orchestrates Sidecarless eBPF performance tests.
#
# Experiment Flow:
# 1. Kill any existing 'socat' proxy and flush NAT redirect tables inside 'ns_victim'.
# 2. Compile and attach the eBPF map-contention program to host-side veths using attach_ebpf.sh.
# 3. Spawn Victim (Python HTTP) and Attacker (sleep daemon) runc containers inside pre-created netns.
# 4. Start a dummy web server on the host (10.0.0.1:8000) for the Attacker to load test.
# 5. For each Attacker RPS [0, 10000, 20000, 30000]:
#    a. Exec 'wrk2' inside the Attacker container targeting the host's dummy server.
#    b. Spin up background 'bpftrace' specifically monitoring eBPF map lookup/update
#       execution latency and queued_spin_lock_slowpath calls.
#    c. Measure Victim response time using 'fortio' against port 80 directly (bypassing proxies).
#    d. Tear down background load and track results in a timestamped folder.
# 6. Detach eBPF classifiers after completion.

set -euo pipefail

# Experiment options
RPS_ARR=(0 10000 20000 30000)
DURATION_SEC=30
WARMUP_SEC=2
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULTS_DIR="results/sidecarless/$TIMESTAMP"

if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run as root to control OCI containers and run bpftrace." >&2
    exit 1
fi

# Check for host requirements
for cmd in runc fortio bpftrace clang tc conntrack; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: Missing required host tool: '$cmd'." >&2
        exit 1
    fi
done

# Ensure results directory exists
mkdir -p "$RESULTS_DIR"

# Step 1: Remove Sidecar proxy and NAT redirects
echo "Step 1: Cleaning up any Sidecar proxies and NAT rules..."
pkill -9 -f "socat TCP-LISTEN:8080" 2>/dev/null || true
ip netns exec ns_victim iptables -t nat -F 2>/dev/null || true

# Step 2: Compile and attach eBPF router program
echo "Step 2: Attaching eBPF classifier..."
./attach_ebpf.sh

# Step 3: Ensure OCI containers are running
echo "Step 3: Spawning runc OCI containers..."
# Kill any lingering containers
runc kill victim_container KILL 2>/dev/null || true
runc delete victim_container 2>/dev/null || true
runc kill attacker_container KILL 2>/dev/null || true
runc delete attacker_container 2>/dev/null || true

# Start containers in background
runc run --bundle victim_bundle -d victim_container
runc run --bundle attacker_bundle -d attacker_container

# Step 4: Start host dummy web server for Attacker to target
echo "Step 4: Launching dummy server on host..."
python3 -m http.server --bind 10.0.0.1 8000 &>/dev/null &
DUMMY_PID=$!

# Helper to clean up all background jobs on premature script exit
cleanup_trap() {
    echo "Aborting! Cleaning up background jobs and containers..."
    kill -9 "$DUMMY_PID" 2>/dev/null || true
    runc kill victim_container KILL 2>/dev/null || true
    runc delete victim_container 2>/dev/null || true
    runc kill attacker_container KILL 2>/dev/null || true
    runc delete attacker_container 2>/dev/null || true
    tc qdisc del dev veth-vic-br clsact 2>/dev/null || true
    tc qdisc del dev veth-att-br clsact 2>/dev/null || true
    rm -f /sys/fs/bpf/tc/globals/flow_map /sys/fs/bpf/ip/globals/flow_map 2>/dev/null || true
}
trap cleanup_trap INT TERM

# Step 5: Loop through the background load test matrix
echo "Step 5: Executing test matrix..."
for rps in "${RPS_ARR[@]}"; do
    echo "=========================================================="
    echo "Running Sidecarless eBPF benchmark with Attacker Load: $rps RPS"
    echo "=========================================================="
    
    WRK2_PID=""
    
    # 5a. Start Attacker Load if RPS > 0
    if [ "$rps" -gt 0 ]; then
        echo "Starting background attacker load ($rps RPS)..."
        # Run slightly longer than the measurement duration to guarantee load throughout
        WRK2_DUR=$((DURATION_SEC + 5))
        runc exec attacker_container wrk2 -t2 -c100 -d"${WRK2_DUR}s" -R "$rps" http://10.0.0.1:8000/ &>/dev/null &
        WRK2_PID=$!
        sleep "$WARMUP_SEC"
    fi
    
    # 5b. Launch bpftrace in the background to track eBPF hash map lookup/update execution latency
    # and queued_spin_lock_slowpath counts (indicating spinlock contention).
    echo "Starting background bpftrace eBPF map & lock contention tracker..."
    bpftrace -e '
    kprobe:htab_map_lookup_elem {
        @lookup_start[tid] = nsecs;
    }
    kretprobe:htab_map_lookup_elem {
        if (@lookup_start[tid]) {
            @lookup_latency_ns = hist(nsecs - @lookup_start[tid]);
            delete(@lookup_start[tid]);
        }
    }
    kprobe:htab_map_update_elem {
        @update_start[tid] = nsecs;
    }
    kretprobe:htab_map_update_elem {
        if (@update_start[tid]) {
            @update_latency_ns = hist(nsecs - @update_start[tid]);
            delete(@update_start[tid]);
        }
    }
    kprobe:queued_spin_lock_slowpath {
        @spinlock_contention_count = count();
    }
    ' &> "$RESULTS_DIR/bpftrace_rps_${rps}.log" &
    BPFTRACE_PID=$!
    sleep 1
    
    # 5c. Run Fortio Measurement directly against the Victim container on port 80
    echo "Running fortio latency measurements for ${DURATION_SEC}s..."
    fortio load -c 10 -qps 500 -t "${DURATION_SEC}s" -json "$RESULTS_DIR/fortio_rps_${rps}.json" http://10.0.0.10:80/
    
    # 5d. Tear down load and bpftrace for this iteration
    echo "Stopping measurements..."
    kill -2 "$BPFTRACE_PID" || true
    wait "$BPFTRACE_PID" 2>/dev/null || true
    
    if [ -n "$WRK2_PID" ]; then
        kill "$WRK2_PID" 2>/dev/null || true
        wait "$WRK2_PID" 2>/dev/null || true
    fi
    
    # Reset network connections and tables to prevent pollution
    echo "Resetting conntrack state and resting..."
    conntrack -F 2>/dev/null || true
    sleep 3
done

# Step 6: Final clean up and detach eBPF
echo "Step 6: Final cleanup and detaching eBPF..."
trap - INT TERM
kill -9 "$DUMMY_PID" 2>/dev/null || true

runc kill victim_container KILL 2>/dev/null || true
runc delete victim_container 2>/dev/null || true
runc kill attacker_container KILL 2>/dev/null || true
runc delete attacker_container 2>/dev/null || true

tc qdisc del dev veth-vic-br clsact 2>/dev/null || true
tc qdisc del dev veth-att-br clsact 2>/dev/null || true
rm -f /sys/fs/bpf/tc/globals/flow_map /sys/fs/bpf/ip/globals/flow_map 2>/dev/null || true

echo "Sidecarless eBPF experiment completed. Results stored in $RESULTS_DIR"
