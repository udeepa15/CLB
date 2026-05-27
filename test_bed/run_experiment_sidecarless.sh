#!/usr/bin/env bash
# run_experiment_sidecarless.sh: Orchestrates Sidecarless eBPF performance tests.
#
# Experiment Flow:
# 1. Kill any existing 'socat' proxy and flush NAT redirect tables inside 'ns_victim'.
# 2. Compile and attach the eBPF map-contention (shared-key) program to host-side veths.
# 3. Spawn Victim (Python HTTP) and Attacker (sleep daemon) runc containers inside pre-created netns.
# 4. Start an iperf3 server on the host (10.0.0.1:8000), CPU-pinned, as the Attacker target.
# 5. For each Attacker load level [0, 1G, 2G, 4G, 8G]:
#    a. Exec 'iperf3' UDP flood inside the Attacker container, CPU-pinned, targeting the host.
#    b. Spin up background 'bpftrace' specifically monitoring eBPF map lookup/update
#       execution latency and queued_spin_lock_slowpath calls.
#    c. Measure Victim response time using 'fortio' against port 80 directly (bypassing proxies).
#    d. Tear down background load and track results in a timestamped folder.
# 6. Detach eBPF classifiers after completion.

set -euo pipefail

# Experiment options
# CPU_CORE_SET: pin all load-generating processes to these cores to force SoftIRQ saturation.
# Use a single core (e.g. "0") for maximum contention; "0,1" for two-core pressure.
CPU_CORE_SET="0,1"

# LOAD_ARR: iperf3 -b target bandwidths that replace the old wrk2 RPS values.
# 0 means no attacker load; non-zero strings are passed directly to iperf3 -b.
LOAD_ARR=(0 1G 2G 4G 8G)
DURATION_SEC=30
WARMUP_SEC=2
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULTS_DIR="results/sidecarless/$TIMESTAMP"

if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run as root to control OCI containers and run bpftrace." >&2
    exit 1
fi

# Check for host requirements
for cmd in runc fortio bpftrace clang tc conntrack iperf3 taskset; do
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

# Ensure the victim loopback interface is up after runc starts the namespace.
ip netns exec ns_victim ip link set dev lo up

echo "Waiting for Python HTTP server inside the container to bind to port 80..."
for i in {1..40}; do
    if ip netns exec ns_victim nc -z 127.0.0.1 80 2>/dev/null; then
        echo "Python HTTP server is up!"
        break
    fi
    if [ "$i" -eq 40 ]; then
        echo "ERROR: Python HTTP server failed to start inside container." >&2
        exit 1
    fi
    sleep 0.5
done

# Step 4: Start iperf3 server on the host for the Attacker to flood.
# CPU-pinned to CPU_CORE_SET to force SoftIRQ processing onto those cores.
echo "Step 4: Launching iperf3 server on host (CPU-pinned to cores ${CPU_CORE_SET})..."
taskset -c "$CPU_CORE_SET" iperf3 -s -B 10.0.0.1 -p 8000 -D
# iperf3 -D daemonises; capture its PID via pgrep so we can kill it later.
sleep 0.5
IPERF_SERVER_PID=$(pgrep -n -f 'iperf3 -s' || true)

# Helper to clean up all background jobs on premature script exit
cleanup_trap() {
    echo "Aborting! Cleaning up background jobs and containers..."
    [ -n "${IPERF_SERVER_PID:-}" ] && kill -9 "$IPERF_SERVER_PID" 2>/dev/null || true
    pkill -9 -f 'iperf3 -s' 2>/dev/null || true
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
for load in "${LOAD_ARR[@]}"; do
    echo "=========================================================="
    echo "Running Sidecarless eBPF benchmark with Attacker Load: ${load}"
    echo "=========================================================="

    ATTACKER_PID=""

    # 5a. Start Attacker UDP flood if load > 0
    # iperf3 is run CPU-pinned via taskset so SoftIRQs are processed on CPU_CORE_SET,
    # preventing the CFS scheduler from offloading them to idle cores.
    if [ "$load" != "0" ]; then
        echo "Starting background iperf3 UDP flood (bandwidth: ${load}, CPUs: ${CPU_CORE_SET})..."
        WRK2_DUR=$((DURATION_SEC + 5))
        # Run iperf3 client inside the attacker container; use taskset on the host wrapper
        # so the runc exec process (and its SoftIRQ descendants) are locked to CPU_CORE_SET.
        taskset -c "$CPU_CORE_SET" runc exec attacker_container \
            iperf3 -c 10.0.0.1 -p 8000 -u -b "${load}" -t "${WRK2_DUR}" &>/dev/null &
        ATTACKER_PID=$!
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
    kprobe:*queued_spin_lock_slowpath* {
        @spinlock_contention_count = count();
    }
    ' &> "$RESULTS_DIR/bpftrace_load_${load}.log" &
    BPFTRACE_PID=$!
    sleep 1

    # 5c. Run Fortio Measurement directly against the Victim container on port 80
    echo "Running fortio latency measurements for ${DURATION_SEC}s..."
    taskset -c "$CPU_CORE_SET" fortio load -c 10 -qps 500 -t "${DURATION_SEC}s" \
        -json "$RESULTS_DIR/fortio_load_${load}.json" http://10.0.0.10:80/

    # 5d. Tear down load and bpftrace for this iteration
    echo "Stopping measurements..."
    kill -2 "$BPFTRACE_PID" || true
    wait "$BPFTRACE_PID" 2>/dev/null || true

    if [ -n "$ATTACKER_PID" ]; then
        kill "$ATTACKER_PID" 2>/dev/null || true
        wait "$ATTACKER_PID" 2>/dev/null || true
    fi

    # Reset network connections and tables to prevent pollution
    echo "Resetting conntrack state and resting..."
    conntrack -F 2>/dev/null || true
    sleep 3
done

# Step 6: Final clean up and detach eBPF
echo "Step 6: Final cleanup and detaching eBPF..."
trap - INT TERM
[ -n "${IPERF_SERVER_PID:-}" ] && kill -9 "$IPERF_SERVER_PID" 2>/dev/null || true
pkill -9 -f 'iperf3 -s' 2>/dev/null || true

runc kill victim_container KILL 2>/dev/null || true
runc delete victim_container 2>/dev/null || true
runc kill attacker_container KILL 2>/dev/null || true
runc delete attacker_container 2>/dev/null || true

tc qdisc del dev veth-vic-br clsact 2>/dev/null || true
tc qdisc del dev veth-att-br clsact 2>/dev/null || true
rm -f /sys/fs/bpf/tc/globals/flow_map /sys/fs/bpf/ip/globals/flow_map 2>/dev/null || true

echo "Sidecarless eBPF experiment completed. Results stored in $RESULTS_DIR"
