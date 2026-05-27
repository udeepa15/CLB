#!/usr/bin/env bash
# run_experiment_sidecar.sh  —  Sidecar Proxy Isolation Baseline.
#
# TRAFFIC PATH:
#   Same hping3 flood from ns_attacker -> ns_victim via bridge.
#   BUT: NO eBPF attached. Kernel forwards packets normally — no spinlock overhead.
#   socat proxy provides connection-level isolation for TCP (fortio) traffic.
#   Result: victim HTTP latency should stay flat as flood increases.

set -euo pipefail

CPU_CORE_SET="0,1"
FLOOD_ARR=(0 u1000 u500 u200 u100 u50 u20 u10 u5 u2 u1)
FORTIO_QPS=500
FORTIO_CONNS=10
DURATION_SEC=30
WARMUP_SEC=3
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULTS_DIR="results/sidecar/$TIMESTAMP"

if [ "$EUID" -ne 0 ]; then echo "ERROR: Must be run as root." >&2; exit 1; fi

for cmd in runc fortio bpftrace socat conntrack hping3 taskset; do
    command -v "$cmd" &>/dev/null || { echo "ERROR: Missing '$cmd'." >&2; exit 1; }
done

mkdir -p "$RESULTS_DIR"

echo "Step 1: Detaching eBPF (baseline = NO eBPF spinlock)..."
pkill -9 -f "socat TCP-LISTEN:8080" 2>/dev/null || true
for dev in veth-vic-br veth-att-br br-mesh; do tc qdisc del dev "$dev" clsact 2>/dev/null || true; done
rm -f /sys/fs/bpf/tc/globals/flow_map /sys/fs/bpf/ip/globals/flow_map 2>/dev/null || true

echo "Step 2: Spawning runc containers..."
runc kill victim_container   KILL 2>/dev/null || true; runc delete victim_container   2>/dev/null || true
runc kill attacker_container KILL 2>/dev/null || true; runc delete attacker_container 2>/dev/null || true
runc run --bundle victim_bundle   -d victim_container  > /tmp/runc-victim.log  2>&1
runc run --bundle attacker_bundle -d attacker_container > /tmp/runc-attacker.log 2>&1
ip netns exec ns_victim ip link set dev lo up

echo "Step 3: Configuring NAT redirect (port 80 -> socat 8080)..."
ip netns exec ns_victim iptables -t nat -F PREROUTING 2>/dev/null || true
ip netns exec ns_victim iptables -t nat -A PREROUTING \
    -i veth-victim -p tcp --dport 80 -j REDIRECT --to-ports 8080

echo "Step 4: Starting socat sidecar proxy..."
for i in {1..40}; do
    ip netns exec ns_victim nc -z 127.0.0.1 80 2>/dev/null && { echo "HTTP server up."; break; }
    [ "$i" -eq 40 ] && { echo "ERROR: HTTP server never started." >&2; exit 1; }
    sleep 0.5
done
ip netns exec ns_victim taskset -c 0 \
    socat TCP-LISTEN:8080,fork,reuseaddr,retry=5 TCP:127.0.0.1:80 &
SOCAT_PID=$!
sleep 0.5

cleanup_trap() {
    echo "Cleaning up..."
    kill -9 "$SOCAT_PID" 2>/dev/null || true
    pkill -9 -f 'hping3'  2>/dev/null || true
    runc kill victim_container   KILL 2>/dev/null || true; runc delete victim_container   2>/dev/null || true
    runc kill attacker_container KILL 2>/dev/null || true; runc delete attacker_container 2>/dev/null || true
    ip netns exec ns_victim iptables -t nat -F 2>/dev/null || true
}
trap cleanup_trap INT TERM

echo "Step 5: Executing test matrix..."
for flood_arg in "${FLOOD_ARR[@]}"; do
    echo "=========================================================="
    echo "Sidecar Proxy | hping3 flood from ns_attacker: ${flood_arg}"
    echo "=========================================================="

    ATTACKER_PID=""

    if [ "$flood_arg" != "0" ]; then
        echo "  Starting hping3 flood from inside ns_attacker -> 10.0.0.10 (interval=${flood_arg})..."
        taskset -c 1 ip netns exec ns_attacker hping3 --udp -p 9999 --interval "${flood_arg}" 10.0.0.10 &>/dev/null &
        P1=$!
        taskset -c 1 ip netns exec ns_attacker hping3 --udp -p 9999 --interval "${flood_arg}" 10.0.0.10 &>/dev/null &
        P2=$!
        taskset -c 1 ip netns exec ns_attacker hping3 --udp -p 9999 --interval "${flood_arg}" 10.0.0.10 &>/dev/null &
        P3=$!
        ATTACKER_PID="$P1 $P2 $P3" 
        sleep "$WARMUP_SEC"
    fi

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
    ' &> "$RESULTS_DIR/bpftrace_load_${flood_arg}.log" &
    BPFTRACE_PID=$!
    sleep 1

    echo "  Running fortio at ${FORTIO_QPS} QPS for ${DURATION_SEC}s..."
    taskset -c 0 fortio load \
        -c "${FORTIO_CONNS}" -qps "${FORTIO_QPS}" -t "${DURATION_SEC}s" \
        -json "$RESULTS_DIR/fortio_load_${flood_arg}.json" \
        http://10.0.0.10:80/

    kill -2 "$BPFTRACE_PID" 2>/dev/null || true
    wait  "$BPFTRACE_PID"   2>/dev/null || true

    if [ -n "$ATTACKER_PID" ]; then
        kill -9 $ATTACKER_PID 2>/dev/null || true
        pkill -9 -f 'hping3'    2>/dev/null || true
        wait $ATTACKER_PID    2>/dev/null || true
    fi

    conntrack -F 2>/dev/null || true
    sleep 3
done

echo "Final cleanup..."
trap - INT TERM
kill -9 "$SOCAT_PID" 2>/dev/null || true
pkill -9 -f 'hping3'  2>/dev/null || true
ip netns exec ns_victim iptables -t nat -F 2>/dev/null || true
runc kill victim_container   KILL 2>/dev/null || true; runc delete victim_container   2>/dev/null || true
runc kill attacker_container KILL 2>/dev/null || true; runc delete attacker_container 2>/dev/null || true

echo "Sidecar experiment complete. Results: $RESULTS_DIR"
