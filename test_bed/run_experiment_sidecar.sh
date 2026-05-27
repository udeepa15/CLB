#!/usr/bin/env bash
# run_experiment_sidecar.sh  —  Sidecar proxy isolation baseline.
#
# DESIGN RATIONALE:
#   Identical attacker setup to sidecarless: hping3 --flood from HOST -> 10.0.0.10.
#   BUT: no eBPF is attached. The flood stresses kernel SoftIRQs and NIC queues
#   equally, but WITHOUT the shared htab spinlock serialisation point.
#   The socat sidecar proxy provides connection-level isolation:
#     - attacker UDP is dropped by kernel (no listener on 9999, no eBPF cost)
#     - fortio TCP goes through socat's buffered connection -> python HTTP
#   Result: victim latency stays FLAT as flood increases (the isolation baseline).

set -euo pipefail

# ── Tuning knobs (MUST match sidecarless for fair comparison) ─────────────────
CPU_CORE_SET="0"
FLOOD_ARR=(0 u100 u50 u10 u1)
FORTIO_QPS=500
FORTIO_CONNS=10
DURATION_SEC=30
WARMUP_SEC=3
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULTS_DIR="results/sidecar/$TIMESTAMP"

# ── Sanity checks ─────────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Must be run as root." >&2; exit 1
fi

for cmd in runc fortio bpftrace socat conntrack hping3 taskset; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: Missing required host tool: '$cmd'." >&2; exit 1
    fi
done

mkdir -p "$RESULTS_DIR"

# ── Step 1: Detach eBPF (baseline = no eBPF spinlock) ────────────────────────
echo "Step 1: Detaching eBPF, killing old proxies..."
pkill -9 -f "socat TCP-LISTEN:8080" 2>/dev/null || true
tc qdisc del dev veth-vic-br clsact 2>/dev/null || true
tc qdisc del dev veth-att-br clsact 2>/dev/null || true
rm -f /sys/fs/bpf/tc/globals/flow_map /sys/fs/bpf/ip/globals/flow_map 2>/dev/null || true

# ── Step 2: Start runc containers ────────────────────────────────────────────
echo "Step 2: Spawning runc OCI containers..."
runc kill victim_container   KILL 2>/dev/null || true; runc delete victim_container   2>/dev/null || true
runc kill attacker_container KILL 2>/dev/null || true; runc delete attacker_container 2>/dev/null || true

runc run --bundle victim_bundle   -d victim_container  > /tmp/runc-victim.log  2>&1
runc run --bundle attacker_bundle -d attacker_container > /tmp/runc-attacker.log 2>&1
ip netns exec ns_victim ip link set dev lo up

# ── Step 3: Configure sidecar NAT redirect ───────────────────────────────────
echo "Step 3: Configuring NAT redirect port 80 -> socat 8080..."
ip netns exec ns_victim iptables -t nat -F PREROUTING 2>/dev/null || true
ip netns exec ns_victim iptables -t nat -A PREROUTING     -i veth-victim -p tcp --dport 80 -j REDIRECT --to-ports 8080

# ── Step 4: Start socat sidecar proxy ────────────────────────────────────────
echo "Step 4: Starting socat sidecar proxy..."
for i in {1..40}; do
    ip netns exec ns_victim nc -z 127.0.0.1 80 2>/dev/null && { echo "HTTP server up."; break; }
    [ "$i" -eq 40 ] && { echo "ERROR: HTTP server never started." >&2; exit 1; }
    sleep 0.5
done

ip netns exec ns_victim taskset -c "$CPU_CORE_SET"     socat TCP-LISTEN:8080,fork,reuseaddr,retry=5 TCP:127.0.0.1:80 &
SOCAT_PID=$!
sleep 0.5

# ── Cleanup trap ─────────────────────────────────────────────────────────────
cleanup_trap() {
    echo "Aborting! Cleaning up..."
    kill -9 "$SOCAT_PID" 2>/dev/null || true
    pkill -9 -f 'hping3'  2>/dev/null || true
    runc kill victim_container   KILL 2>/dev/null || true; runc delete victim_container   2>/dev/null || true
    runc kill attacker_container KILL 2>/dev/null || true; runc delete attacker_container 2>/dev/null || true
    ip netns exec ns_victim iptables -t nat -F 2>/dev/null || true
}
trap cleanup_trap INT TERM

# ── Step 5: Test matrix ───────────────────────────────────────────────────────
echo "Step 5: Executing test matrix..."
for flood_arg in "${FLOOD_ARR[@]}"; do
    echo "=========================================================="
    echo "Sidecar Proxy | Attacker hping3 flood: ${flood_arg}"
    echo "=========================================================="

    ATTACKER_PID=""

    # 5a. Launch HOST-SIDE hping3 UDP flood (same as sidecarless for fair comparison)
    if [ "$flood_arg" != "0" ]; then
        echo "  Launching hping3 --udp --interval ${flood_arg} to 10.0.0.10 (CPU core ${CPU_CORE_SET})..."
        taskset -c "$CPU_CORE_SET" hping3 --udp -p 9999             --interval "${flood_arg}" 10.0.0.10 &>/dev/null &
        ATTACKER_PID=$!
        sleep "$WARMUP_SEC"
    fi

    # 5b. bpftrace: scheduling context switches + NET_RX softirq latency
    echo "  Starting bpftrace scheduler/softirq monitor..."
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
    ' &> "$RESULTS_DIR/bpftrace_load_${flood_arg}.log" &
    BPFTRACE_PID=$!
    sleep 1

    # 5c. Fortio: measure victim HTTP through socat sidecar at high QPS
    echo "  Running fortio at ${FORTIO_QPS} QPS, ${FORTIO_CONNS} connections for ${DURATION_SEC}s..."
    taskset -c "$CPU_CORE_SET" fortio load         -c "${FORTIO_CONNS}" -qps "${FORTIO_QPS}" -t "${DURATION_SEC}s"         -json "$RESULTS_DIR/fortio_load_${flood_arg}.json"         http://10.0.0.10:80/

    # 5d. Tear down
    kill -2 "$BPFTRACE_PID" 2>/dev/null || true
    wait  "$BPFTRACE_PID"   2>/dev/null || true

    if [ -n "$ATTACKER_PID" ]; then
        kill -9 "$ATTACKER_PID" 2>/dev/null || true
        pkill -9 -f 'hping3'    2>/dev/null || true
        wait "$ATTACKER_PID"    2>/dev/null || true
    fi

    conntrack -F 2>/dev/null || true
    sleep 3
done

# ── Final cleanup ─────────────────────────────────────────────────────────────
echo "Final cleanup..."
trap - INT TERM
kill -9 "$SOCAT_PID" 2>/dev/null || true
pkill -9 -f 'hping3'  2>/dev/null || true
ip netns exec ns_victim iptables -t nat -F 2>/dev/null || true
runc kill victim_container   KILL 2>/dev/null || true; runc delete victim_container   2>/dev/null || true
runc kill attacker_container KILL 2>/dev/null || true; runc delete attacker_container 2>/dev/null || true

echo "Sidecar experiment complete. Results: $RESULTS_DIR"
