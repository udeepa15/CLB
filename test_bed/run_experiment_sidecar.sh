#!/usr/bin/env bash
# run_experiment_sidecar.sh: Orchestrates traditional Sidecar proxy baseline performance tests.
#
# Experiment Flow:
# 1. Clean and detach any existing eBPF TC filters to isolate Sidecar proxy overhead.
# 2. Spawn Victim (Python HTTP) and Attacker (sleep daemon) runc containers inside pre-created netns.
# 3. Configure transparent redirection inside 'ns_victim': Redirect TCP traffic destined for 80 -> 8080.
# 4. Start local proxy 'socat' in 'ns_victim' CPU-pinned to listen on 8080 and forward to 127.0.0.1:80.
# 5. Start an iperf3 server on the host (10.0.0.1:8000), CPU-pinned, as the Attacker target.
# 6. For each Attacker load level [0, 1G, 2G, 4G, 8G]:
#    a. Exec 'iperf3' UDP flood inside the Attacker container, CPU-pinned, targeting the host.
#    b. Spin up background 'bpftrace' to record kernel CPU scheduling and softirq latencies.
#    c. Measure Victim response time using 'fortio' against the transparently proxied port 80.
#    d. Tear down background load and track results in a timestamped folder.

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
RESULTS_DIR="results/sidecar/$TIMESTAMP"

if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run as root to control OCI containers and run bpftrace." >&2
    exit 1
fi

# Check for host requirements
for cmd in runc fortio bpftrace socat conntrack iperf3 taskset; do
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
runc run --bundle victim_bundle -d victim_container > /tmp/runc-start-victim.log 2>&1
runc run --bundle attacker_bundle -d attacker_container > /tmp/runc-start-attacker.log 2>&1

# Ensure the victim loopback interface is up after runc starts the namespace.
ip netns exec ns_victim ip link set dev lo up

# Step 3: Configure transparent routing in ns_victim
echo "Step 3: Configuring transparent redirect inside ns_victim..."
ip netns exec ns_victim iptables -t nat -F PREROUTING 2>/dev/null || true
ip netns exec ns_victim iptables -t nat -A PREROUTING -i veth-victim -p tcp --dport 80 -j REDIRECT --to-ports 8080

# Step 4: Spawn Sidecar proxy (socat) in ns_victim
echo "Step 4: Launching socat proxy in ns_victim..."

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

# Launch socat with explicit retry and tuning parameters.
# CPU-pinned so the proxy's SoftIRQ and syscall processing competes on the same cores as the flood.
ip netns exec ns_victim taskset -c "$CPU_CORE_SET" socat TCP-LISTEN:8080,fork,reuseaddr,retry=5 TCP:127.0.0.1:80 &
SOCAT_PID=$!

# Give socat a split second to open port 8080
sleep 0.5

# Step 5: Start iperf3 server on the host for the Attacker to flood.
# CPU-pinned to CPU_CORE_SET to force SoftIRQ processing onto those cores.
echo "Step 5: Launching iperf3 server on host (CPU-pinned to cores ${CPU_CORE_SET})..."
taskset -c "$CPU_CORE_SET" iperf3 -s -B 10.0.0.1 -p 8000 -D
# iperf3 -D daemonises; capture its PID via pgrep so we can kill it later.
sleep 0.5
IPERF_SERVER_PID=$(pgrep -n -f 'iperf3 -s' || true)

# Helper to clean up all background jobs on premature script exit
cleanup_trap() {
    echo "Aborting! Cleaning up background jobs and containers..."
    kill -9 "$SOCAT_PID" 2>/dev/null || true
    [ -n "${IPERF_SERVER_PID:-}" ] && kill -9 "$IPERF_SERVER_PID" 2>/dev/null || true
    pkill -9 -f 'iperf3 -s' 2>/dev/null || true
    runc kill victim_container KILL 2>/dev/null || true
    runc delete victim_container 2>/dev/null || true
    runc kill attacker_container KILL 2>/dev/null || true
    runc delete attacker_container 2>/dev/null || true
    ip netns exec ns_victim iptables -t nat -F 2>/dev/null || true
}
trap cleanup_trap INT TERM

# Step 6: Loop through the background load test matrix
echo "Step 6: Executing test matrix..."
for load in "${LOAD_ARR[@]}"; do
    echo "=========================================================="
    echo "Running Sidecar Proxy benchmark with Attacker Load: ${load}"
    echo "=========================================================="

    ATTACKER_PID=""

    # 6a. Start Attacker UDP flood if load > 0
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
    ' &> "$RESULTS_DIR/bpftrace_load_${load}.log" &
    BPFTRACE_PID=$!
    sleep 1

    # 6c. Run Fortio Measurement against the proxy (port 80 NAT redirected to socat 8080)
    echo "Running fortio latency measurements for ${DURATION_SEC}s..."
    taskset -c "$CPU_CORE_SET" fortio load -c 10 -qps 500 -t "${DURATION_SEC}s" \
        -json "$RESULTS_DIR/fortio_load_${load}.json" http://10.0.0.10:80/

    # 6d. Tear down load and bpftrace for this iteration
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

# Step 7: Final clean up
echo "Step 7: Final cleanup..."
trap - INT TERM
kill -9 "$SOCAT_PID" 2>/dev/null || true
[ -n "${IPERF_SERVER_PID:-}" ] && kill -9 "$IPERF_SERVER_PID" 2>/dev/null || true
pkill -9 -f 'iperf3 -s' 2>/dev/null || true
ip netns exec ns_victim iptables -t nat -F 2>/dev/null || true

runc kill victim_container KILL 2>/dev/null || true
runc delete victim_container 2>/dev/null || true
runc kill attacker_container KILL 2>/dev/null || true
runc delete attacker_container 2>/dev/null || true

echo "Sidecar proxy experiment completed. Results stored in $RESULTS_DIR"
