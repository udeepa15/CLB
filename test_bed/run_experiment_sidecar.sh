#!/usr/bin/env bash
# run_experiment_sidecar.sh: Orchestrates Sidecar proxy isolation baseline tests.
#
# ISOLATION BASELINE DESIGN:
#   No eBPF is attached. The sidecar (socat) creates a separate TCP connection
#   between client and backend, providing application-level flow isolation.
#   The same attacker UDP flood is directed at 10.0.0.10:9999, but without the
#   eBPF shared spinlock, kernel processing is standard and socat buffers absorb
#   transient jitter -> victim latency stays stable (the isolation baseline).
#
# Experiment Flow:
# 1. Detach any eBPF programs from tc interfaces.
# 2. Spawn victim (Python HTTP) and attacker (sleep) runc containers.
# 3. Configure NAT redirect in ns_victim: TCP port 80 -> socat on 8080.
# 4. Start socat sidecar proxy in ns_victim (CPU-pinned).
# 5. Start iperf3 UDP server INSIDE ns_victim on port 9999.
# 6. For each load level [0, 1G, 2G, 4G, 8G]:
#    a. Run iperf3 UDP flood from attacker container -> 10.0.0.10:9999.
#    b. Collect bpftrace sched/softirq metrics.
#    c. Measure victim HTTP latency with fortio -> 10.0.0.10:80.
#    d. Tear down and iterate.
# 7. Final cleanup.

set -euo pipefail

# ── Tuning knobs (MUST match sidecarless script for fair comparison) ──────────
CPU_CORE_SET="0"
LOAD_ARR=(0 1G 2G 4G 8G)
FORTIO_QPS=2000
DURATION_SEC=30
WARMUP_SEC=3
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULTS_DIR="results/sidecar/$TIMESTAMP"

# ── Sanity checks ─────────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Must be run as root." >&2
    exit 1
fi

for cmd in runc fortio bpftrace socat conntrack iperf3 taskset; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: Missing required host tool: '$cmd'." >&2
        exit 1
    fi
done

mkdir -p "$RESULTS_DIR"

# ── Step 1: Detach eBPF (baseline = no eBPF overhead) ────────────────────────
echo "Step 1: Detaching eBPF programs and killing old proxies..."
pkill -9 -f "socat TCP-LISTEN:8080" 2>/dev/null || true
tc qdisc del dev veth-vic-br clsact 2>/dev/null || true
tc qdisc del dev veth-att-br clsact 2>/dev/null || true
rm -f /sys/fs/bpf/tc/globals/flow_map /sys/fs/bpf/ip/globals/flow_map 2>/dev/null || true

# ── Step 2: Start runc containers ────────────────────────────────────────────
echo "Step 2: Spawning runc OCI containers..."
runc kill victim_container   KILL 2>/dev/null || true
runc delete victim_container       2>/dev/null || true
runc kill attacker_container KILL  2>/dev/null || true
runc delete attacker_container     2>/dev/null || true

runc run --bundle victim_bundle   -d victim_container  > /tmp/runc-victim.log  2>&1
runc run --bundle attacker_bundle -d attacker_container > /tmp/runc-attacker.log 2>&1

ip netns exec ns_victim ip link set dev lo up

# ── Step 3: Configure NAT redirect in ns_victim ──────────────────────────────
echo "Step 3: Configuring sidecar NAT redirect (port 80 -> 8080)..."
ip netns exec ns_victim iptables -t nat -F PREROUTING 2>/dev/null || true
ip netns exec ns_victim iptables -t nat -A PREROUTING     -i veth-victim -p tcp --dport 80 -j REDIRECT --to-ports 8080

# ── Step 4: Start socat sidecar proxy ────────────────────────────────────────
echo "Step 4: Waiting for HTTP server then launching socat sidecar..."
for i in {1..40}; do
    ip netns exec ns_victim nc -z 127.0.0.1 80 2>/dev/null && { echo "HTTP server up."; break; }
    [ "$i" -eq 40 ] && { echo "ERROR: HTTP server never started." >&2; exit 1; }
    sleep 0.5
done

# CPU-pinned: sidecar processing competes on the same core as the UDP flood
# to give the sidecar scenario the same CPU pressure as the sidecarless one.
ip netns exec ns_victim taskset -c "$CPU_CORE_SET"     socat TCP-LISTEN:8080,fork,reuseaddr,retry=5 TCP:127.0.0.1:80 &
SOCAT_PID=$!
sleep 0.5

# ── Step 5: Start iperf3 UDP server INSIDE victim netns ─────────────────────
# Identical to sidecarless setup so attacker load is directly comparable.
echo "Step 5: Starting iperf3 UDP server inside ns_victim on port 9999..."
ip netns exec ns_victim iperf3 -s -p 9999 -D
sleep 0.5
IPERF_VICTIM_PID=$(ip netns exec ns_victim pgrep -n -f 'iperf3 -s' 2>/dev/null || true)

# ── Cleanup trap ─────────────────────────────────────────────────────────────
cleanup_trap() {
    echo "Aborting! Cleaning up..."
    kill -9 "$SOCAT_PID" 2>/dev/null || true
    [ -n "${IPERF_VICTIM_PID:-}" ] && kill -9 "$IPERF_VICTIM_PID" 2>/dev/null || true
    ip netns exec ns_victim pkill -9 -f 'iperf3 -s' 2>/dev/null || true
    runc kill victim_container   KILL 2>/dev/null || true
    runc delete victim_container       2>/dev/null || true
    runc kill attacker_container KILL  2>/dev/null || true
    runc delete attacker_container     2>/dev/null || true
    ip netns exec ns_victim iptables -t nat -F 2>/dev/null || true
}
trap cleanup_trap INT TERM

# ── Step 6: Test matrix ───────────────────────────────────────────────────────
echo "Step 6: Executing test matrix..."
for load in "${LOAD_ARR[@]}"; do
    echo "=========================================================="
    echo "Sidecar Proxy | Attacker: ${load} UDP flood -> 10.0.0.10:9999"
    echo "=========================================================="

    ATTACKER_PID=""
    WRK2_DUR=$((DURATION_SEC + 5))

    # 6a. Launch attacker UDP flood toward victim IP
    if [ "$load" != "0" ]; then
        echo "  Starting iperf3 UDP flood: ${load} bandwidth, CPU pinned to core ${CPU_CORE_SET}..."
        taskset -c "$CPU_CORE_SET" runc exec attacker_container \
            iperf3 -c 10.0.0.10 -p 9999 -u -b "${load}" -t "${WRK2_DUR}" &>/dev/null &
        ATTACKER_PID=$!
        sleep "$WARMUP_SEC"
    fi

    # 6b. bpftrace: sched context switches and NET_RX softirq latency
    echo "  Starting bpftrace..."
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

    # 6c. Fortio: measure victim HTTP through the socat sidecar
    echo "  Running fortio at ${FORTIO_QPS} QPS for ${DURATION_SEC}s..."
    taskset -c "$CPU_CORE_SET" fortio load -c 20 -qps "${FORTIO_QPS}" -t "${DURATION_SEC}s" \
        -json "$RESULTS_DIR/fortio_load_${load}.json" http://10.0.0.10:80/

    # 6d. Tear down
    kill -2 "$BPFTRACE_PID" || true
    wait  "$BPFTRACE_PID"   2>/dev/null || true

    if [ -n "$ATTACKER_PID" ]; then
        kill "$ATTACKER_PID" 2>/dev/null || true
        wait "$ATTACKER_PID" 2>/dev/null || true
    fi

    echo "  Resetting conntrack..."
    conntrack -F 2>/dev/null || true
    sleep 3
done

# ── Step 7: Final cleanup ─────────────────────────────────────────────────────
echo "Step 7: Final cleanup..."
trap - INT TERM

kill -9 "$SOCAT_PID" 2>/dev/null || true
[ -n "${IPERF_VICTIM_PID:-}" ] && kill -9 "$IPERF_VICTIM_PID" 2>/dev/null || true
ip netns exec ns_victim pkill -9 -f 'iperf3 -s' 2>/dev/null || true
ip netns exec ns_victim iptables -t nat -F 2>/dev/null || true

runc kill victim_container   KILL 2>/dev/null || true
runc delete victim_container       2>/dev/null || true
runc kill attacker_container KILL  2>/dev/null || true
runc delete attacker_container     2>/dev/null || true

echo "Sidecar experiment complete. Results: $RESULTS_DIR"
