#!/usr/bin/env bash
# run_experiment_sidecarless.sh  —  Isolation Deficit: Sidecarless eBPF Mesh.
#
# TRAFFIC PATH (the key insight):
#   hping3 runs INSIDE ns_attacker (10.0.0.20) -> targets ns_victim (10.0.0.10).
#   Path: ns_attacker -> veth-att-br [eBPF] -> br-mesh [eBPF] -> veth-vic-br [eBPF] -> ns_victim
#   ALL three hops fire the shared_global_key spinlock.
#
#   fortio runs on HOST -> targets 10.0.0.10.
#   Path: host -> br-mesh [eBPF] -> veth-vic-br [eBPF] -> ns_victim
#   Also fires the same spinlock.
#
#   Under heavy hping3 flood, both paths contend on the SAME bucket spinlock
#   -> fortio measurement latency spikes -> Isolation Deficit demonstrated.

set -euo pipefail

CPU_CORE_SET="0,1"

# hping3 --interval values controlling packet rate.
# Sent from INSIDE ns_attacker so traffic traverses the eBPF-hooked bridge.
# 0 = baseline (no attacker), others are hping3 --interval args:
#   u100 ~ 10,000 pps  |  u50 ~ 20,000 pps  |  u10 ~ 100,000 pps  |  u1 ~ 1,000,000 pps
FLOOD_ARR=(0 u100 u50 u10 u1)

FORTIO_QPS=500
FORTIO_CONNS=10
DURATION_SEC=30
WARMUP_SEC=3
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULTS_DIR="results/sidecarless/$TIMESTAMP"

if [ "$EUID" -ne 0 ]; then echo "ERROR: Must be run as root." >&2; exit 1; fi

for cmd in runc fortio bpftrace clang tc conntrack hping3 taskset; do
    command -v "$cmd" &>/dev/null || { echo "ERROR: Missing '$cmd'." >&2; exit 1; }
done

mkdir -p "$RESULTS_DIR"

echo "Step 1: Cleaning up sidecar residue..."
pkill -9 -f "socat TCP-LISTEN:8080" 2>/dev/null || true
ip netns exec ns_victim iptables -t nat -F 2>/dev/null || true

echo "Step 2: Attaching eBPF to veth-vic-br, veth-att-br, AND br-mesh..."
./attach_ebpf.sh

echo "Step 3: Spawning runc containers..."
runc kill victim_container   KILL 2>/dev/null || true; runc delete victim_container   2>/dev/null || true
runc kill attacker_container KILL 2>/dev/null || true; runc delete attacker_container 2>/dev/null || true
runc run --bundle victim_bundle   -d victim_container
runc run --bundle attacker_bundle -d attacker_container
ip netns exec ns_victim ip link set dev lo up

echo "Waiting for Python HTTP server on port 80..."
for i in {1..40}; do
    ip netns exec ns_victim nc -z 127.0.0.1 80 2>/dev/null && { echo "HTTP server up."; break; }
    [ "$i" -eq 40 ] && { echo "ERROR: HTTP server never started." >&2; exit 1; }
    sleep 0.5
done

cleanup_trap() {
    echo "Cleaning up..."
    pkill -9 -f 'hping3' 2>/dev/null || true
    runc kill victim_container   KILL 2>/dev/null || true; runc delete victim_container   2>/dev/null || true
    runc kill attacker_container KILL 2>/dev/null || true; runc delete attacker_container 2>/dev/null || true
    for dev in veth-vic-br veth-att-br br-mesh; do tc qdisc del dev "$dev" clsact 2>/dev/null || true; done
    rm -f /sys/fs/bpf/tc/globals/flow_map /sys/fs/bpf/ip/globals/flow_map 2>/dev/null || true
}
trap cleanup_trap INT TERM

echo "Step 4: Executing test matrix..."
for flood_arg in "${FLOOD_ARR[@]}"; do
    echo "=========================================================="
    echo "Sidecarless eBPF | hping3 flood from ns_attacker: ${flood_arg}"
    echo "=========================================================="

    ATTACKER_PID=""

    if [ "$flood_arg" != "0" ]; then
        echo "  Starting hping3 flood from inside ns_attacker -> 10.0.0.10 (interval=${flood_arg})..."
        # Run hping3 INSIDE ns_attacker namespace so packets traverse:
        # veth-attacker -> veth-att-br [eBPF] -> br-mesh [eBPF] -> veth-vic-br [eBPF] -> ns_victim
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
pkill -9 -f 'hping3' 2>/dev/null || true
runc kill victim_container   KILL 2>/dev/null || true; runc delete victim_container   2>/dev/null || true
runc kill attacker_container KILL 2>/dev/null || true; runc delete attacker_container 2>/dev/null || true
for dev in veth-vic-br veth-att-br br-mesh; do tc qdisc del dev "$dev" clsact 2>/dev/null || true; done
rm -f /sys/fs/bpf/tc/globals/flow_map /sys/fs/bpf/ip/globals/flow_map 2>/dev/null || true

echo "Sidecarless experiment complete. Results: $RESULTS_DIR"
