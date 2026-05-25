#!/usr/bin/env bash
# run_experiment_sidecar.sh: Orchestrates traditional Sidecar proxy baseline performance tests.
#
# Experiment Flow:
# 1. Clean and detach any existing eBPF TC filters to isolate Sidecar proxy overhead.
# 2. Spawn Victim (Python HTTP) and Attacker (sleep daemon) runc containers inside pre-created netns.
# 3. Configure transparent redirection inside 'ns_victim': Redirect TCP traffic destined for 80 -> 8080.
# 4. Start local proxy 'socat' in 'ns_victim' to listen on 8080 and forward to 127.0.0.1:80.
# 5. Start a dummy web server on the host (10.0.0.1:8000) for the Attacker to load test.
# 6. For each Attacker RPS [0, 10000, 20000, 30000]:
#    a. Exec 'wrk2' inside the Attacker container targeting the host's dummy server.
#    b. Spin up background 'bpftrace' to record kernel CPU scheduling and softirq latencies.
#    c. Measure Victim response time using 'fortio' against the transparently proxied port 80.
#    d. Tear down background load and track results in a timestamped folder.

set -euo pipefail

# Experiment options
RPS_ARR=(0 10000 20000 30000)
DURATION_SEC=30
WARMUP_SEC=2
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULTS_DIR="results/sidecar/$TIMESTAMP"

if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run as root to control OCI containers and run bpftrace." >&2
    exit 1
fi

# Check for host requirements
for cmd in runc fortio bpftrace socat conntrack; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: Missing required host tool: '$cmd'." >&2
        exit 1
    fi
done

# Ensure results directory exists
mkdir -p "$RESULTS_DIR"

# Step 1: Clean/Detach eBPF from tc interfaces and kill old proxies
echo "Step 1: Detaching any existing eBPF programs and killing old proxies..."
pkill -9 -f "socat TCP-LISTEN:8080" 2>/dev/null || true
tc qdisc del dev veth-vic-br clsact 2>/dev/null || true
tc qdisc del dev veth-att-br clsact 2>/dev/null || true
rm -f /sys/fs/bpf/tc/globals/flow_map /sys/fs/bpf/ip/globals/flow_map 2>/dev/null || true

# Step 2: Ensure OCI containers are running
echo "Step 2: Spawning runc OCI containers..."
# Kill any lingering containers
runc kill victim_container KILL 2>/dev/null || true
runc delete victim_container 2>/dev/null || true
runc kill attacker_container KILL 2>/dev/null || true
runc delete attacker_container 2>/dev/null || true

# Start containers in background
runc run --bundle victim_bundle -d victim_container
runc run --bundle attacker_bundle -d attacker_container

# Step 3: Configure transparent routing in ns_victim
echo "Step 3: Configuring transparent redirect inside ns_victim..."
ip netns exec ns_victim iptables -t nat -F PREROUTING 2>/dev/null || true
ip netns exec ns_victim iptables -t nat -A PREROUTING -i veth-victim -p tcp --dport 80 -j REDIRECT --to-ports 8080

# Step 4: Spawn Sidecar proxy (socat) in ns_victim
echo "Step 4: Launching socat proxy in ns_victim..."
ip netns exec ns_victim socat TCP-LISTEN:8080,fork,reuseaddr TCP:127.0.0.1:80 &
SOCAT_PID=$!

# Step 5: Start host dummy web server for Attacker to target
echo "Step 5: Launching dummy server on host..."
python3 -m http.server --bind 10.0.0.1 8000 &>/dev/null &
DUMMY_PID=$!

# Helper to clean up all background jobs on premature script exit
cleanup_trap() {
    echo "Aborting! Cleaning up background jobs and containers..."
    kill -9 "$SOCAT_PID" "$DUMMY_PID" 2>/dev/null || true
    runc kill victim_container KILL 2>/dev/null || true
    runc delete victim_container 2>/dev/null || true
    runc kill attacker_container KILL 2>/dev/null || true
    runc delete attacker_container 2>/dev/null || true
    ip netns exec ns_victim iptables -t nat -F 2>/dev/null || true
}
trap cleanup_trap INT TERM

# Step 6: Loop through the background load test matrix
echo "Step 6: Executing test matrix..."
for rps in "${RPS_ARR[@]}"; do
    echo "=========================================================="
    echo "Running Sidecar Proxy benchmark with Attacker Load: $rps RPS"
    echo "=========================================================="
    
    WRK2_PID=""
    
    # 6a. Start Attacker Load if RPS > 0
    if [ "$rps" -gt 0 ]; then
        echo "Starting background attacker load ($rps RPS)..."
        # Run slightly longer than the measurement duration to guarantee load throughout
        WRK2_DUR=$((DURATION_SEC + 5))
        runc exec attacker_container wrk2 -t2 -c100 -d"${WRK2_DUR}s" -R "$rps" http://10.0.0.1:8000/ &>/dev/null &
        WRK2_PID=$!
        sleep "$WARMUP_SEC"
    fi
    
    # 6b. Launch bpftrace in the background to track scheduling context switches and networking softirq latency
    echo "Starting background bpftrace scheduler & softirq tracker..."
    bpftrace -e '
    tracepoint:sched:sched_switch {
        @context_switches = count();
    }
    tracepoint:irq:softirq_entry /args->vec == 3/ {
        @softirq_start[tid] = nsecs;
    }
    tracepoint:irq:softirq_exit /args->vec == 3/ {
        if (@softirq_start[tid]) {
            @net_rx_softirq_latency_ns = hist(nsecs - @softirq_start[tid]);
            delete(@softirq_start[tid]);
        }
    }
    ' &> "$RESULTS_DIR/bpftrace_rps_${rps}.log" &
    BPFTRACE_PID=$!
    sleep 1
    
    # 6c. Run Fortio Measurement against the proxy (port 80 NAT redirected to socat 8080)
    echo "Running fortio latency measurements for ${DURATION_SEC}s..."
    fortio load -c 10 -qps 500 -t "${DURATION_SEC}s" -json "$RESULTS_DIR/fortio_rps_${rps}.json" http://10.0.0.10:80/
    
    # 6d. Tear down load and bpftrace for this iteration
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

# Step 7: Final clean up
echo "Step 7: Final cleanup..."
trap - INT TERM
kill -9 "$SOCAT_PID" "$DUMMY_PID" 2>/dev/null || true
ip netns exec ns_victim iptables -t nat -F 2>/dev/null || true

runc kill victim_container KILL 2>/dev/null || true
runc delete victim_container 2>/dev/null || true
runc kill attacker_container KILL 2>/dev/null || true
runc delete attacker_container 2>/dev/null || true

echo "Sidecar proxy experiment completed. Results stored in $RESULTS_DIR"
