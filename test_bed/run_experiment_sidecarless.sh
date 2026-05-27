#!/usr/bin/env bash
# run_experiment_sidecarless.sh: Orchestrates Sidecarless eBPF isolation-deficit tests.
#
# ISOLATION DEFICIT DESIGN:
#   The attacker floods 10.0.0.10:9999 (victim IP) via iperf3 UDP.
#   Fortio measures the victim HTTP service at 10.0.0.10:80.
#   Both traffic streams traverse veth-vic-br and hit the SAME eBPF
#   shared_global_key spinlock, creating measurable victim latency degradation
#   that grows with attacker bandwidth (the isolation deficit).
#
# Experiment Flow:
# 1. Kill any existing socat proxy / flush NAT rules inside ns_victim.
# 2. Compile and attach the eBPF shared-key contention program to veth-vic-br.
# 3. Spawn victim (Python HTTP) and attacker (sleep) runc containers.
# 4. Start an iperf3 UDP server INSIDE ns_victim on port 9999 (attacker target).
# 5. For each load level [0, 1G, 2G, 4G, 8G]:
#    a. Run iperf3 UDP flood from attacker container -> 10.0.0.10:9999 (SAME veth).
#    b. Collect bpftrace eBPF map contention metrics.
#    c. Measure victim HTTP latency with fortio -> 10.0.0.10:80.
#    d. Tear down and iterate.
# 6. Detach eBPF and clean up.

set -euo pipefail

# ── Tuning knobs ──────────────────────────────────────────────────────────────
# Single core maximises spinlock serialisation: both attacker SoftIRQs and
# fortio packet processing compete on the SAME CPU -> maximum isolation deficit.
CPU_CORE_SET="0"

# Attacker load levels (iperf3 -b target bandwidth).
LOAD_ARR=(0 1G 2G 4G 8G)

# Victim measurement rate. Higher QPS means more contention with the attacker.
FORTIO_QPS=2000

DURATION_SEC=30
WARMUP_SEC=3
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULTS_DIR="results/sidecarless/$TIMESTAMP"

# ── Sanity checks ─────────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Must be run as root." >&2
    exit 1
fi

for cmd in runc fortio bpftrace clang tc conntrack iperf3 taskset; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: Missing required host tool: '$cmd'." >&2
        exit 1
    fi
done

mkdir -p "$RESULTS_DIR"

# ── Step 1: Remove any sidecar residue ───────────────────────────────────────
echo "Step 1: Cleaning up sidecar proxies and NAT rules..."
pkill -9 -f "socat TCP-LISTEN:8080" 2>/dev/null || true
ip netns exec ns_victim iptables -t nat -F 2>/dev/null || true

# ── Step 2: Attach eBPF shared-key contention program ────────────────────────
echo "Step 2: Attaching eBPF classifier (shared_global_key mode)..."
./attach_ebpf.sh

# ── Step 3: Start runc containers ────────────────────────────────────────────
echo "Step 3: Spawning runc OCI containers..."
runc kill victim_container   KILL 2>/dev/null || true
runc delete victim_container       2>/dev/null || true
runc kill attacker_container KILL  2>/dev/null || true
runc delete attacker_container     2>/dev/null || true

runc run --bundle victim_bundle   -d victim_container
runc run --bundle attacker_bundle -d attacker_container

ip netns exec ns_victim ip link set dev lo up

echo "Waiting for Python HTTP server to bind on port 80 inside victim..."
for i in {1..40}; do
    ip netns exec ns_victim nc -z 127.0.0.1 80 2>/dev/null && { echo "HTTP server up."; break; }
    [ "$i" -eq 40 ] && { echo "ERROR: HTTP server never started." >&2; exit 1; }
    sleep 0.5
done

# ── Step 4: Start iperf3 UDP server INSIDE victim netns ─────────────────────
# Attacker will flood 10.0.0.10:9999 (UDP) through veth-vic-br.
# This forces attacker and fortio HTTP traffic onto the SAME interface
# and through the SAME eBPF shared_global_key spinlock.
echo "Step 4: Starting iperf3 UDP server inside ns_victim on port 9999..."
ip netns exec ns_victim iperf3 -s -p 9999 -D
sleep 0.5
IPERF_VICTIM_PID=$(ip netns exec ns_victim pgrep -n -f 'iperf3 -s' 2>/dev/null || true)

# ── Cleanup trap ─────────────────────────────────────────────────────────────
cleanup_trap() {
    echo "Aborting! Cleaning up..."
    [ -n "${IPERF_VICTIM_PID:-}" ] && kill -9 "$IPERF_VICTIM_PID" 2>/dev/null || true
    ip netns exec ns_victim pkill -9 -f 'iperf3 -s' 2>/dev/null || true
    runc kill victim_container   KILL 2>/dev/null || true
    runc delete victim_container       2>/dev/null || true
    runc kill attacker_container KILL  2>/dev/null || true
    runc delete attacker_container     2>/dev/null || true
    tc qdisc del dev veth-vic-br clsact 2>/dev/null || true
    tc qdisc del dev veth-att-br clsact 2>/dev/null || true
    rm -f /sys/fs/bpf/tc/globals/flow_map /sys/fs/bpf/ip/globals/flow_map 2>/dev/null || true
}
trap cleanup_trap INT TERM

# ── Step 5: Test matrix ───────────────────────────────────────────────────────
echo "Step 5: Executing test matrix..."
for load in "${LOAD_ARR[@]}"; do
    echo "=========================================================="
    echo "Sidecarless eBPF | Attacker: ${load} UDP flood -> 10.0.0.10:9999"
    echo "=========================================================="

    ATTACKER_PID=""
    WRK2_DUR=$((DURATION_SEC + 5))

    # 5a. Launch attacker UDP flood toward VICTIM IP (same veth-vic-br path)
    if [ "$load" != "0" ]; then
        echo "  Starting iperf3 UDP flood: ${load} bandwidth, CPU pinned to core ${CPU_CORE_SET}..."
        # taskset pins the runc/iperf3 process so its SoftIRQ handling competes
        # on the SAME core as fortio and the eBPF classifier.
        taskset -c "$CPU_CORE_SET" runc exec attacker_container \
            iperf3 -c 10.0.0.10 -p 9999 -u -b "${load}" -t "${WRK2_DUR}" &>/dev/null &
        ATTACKER_PID=$!
        sleep "$WARMUP_SEC"
    fi

    # 5b. bpftrace: monitor eBPF hash map latency and spinlock slowpath hits
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
    ' &> "$RESULTS_DIR/bpftrace_load_${load}.log" &
    BPFTRACE_PID=$!
    sleep 1

    # 5c. Fortio: measure victim HTTP latency at elevated QPS
    # Higher QPS increases the probability of hitting the spinlock while
    # the attacker holds it, making the isolation deficit measurable.
    echo "  Running fortio at ${FORTIO_QPS} QPS for ${DURATION_SEC}s..."
    taskset -c "$CPU_CORE_SET" fortio load -c 20 -qps "${FORTIO_QPS}" -t "${DURATION_SEC}s" \
        -json "$RESULTS_DIR/fortio_load_${load}.json" http://10.0.0.10:80/

    # 5d. Tear down
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

# ── Step 6: Final cleanup ─────────────────────────────────────────────────────
echo "Step 6: Final cleanup..."
trap - INT TERM

[ -n "${IPERF_VICTIM_PID:-}" ] && kill -9 "$IPERF_VICTIM_PID" 2>/dev/null || true
ip netns exec ns_victim pkill -9 -f 'iperf3 -s' 2>/dev/null || true

runc kill victim_container   KILL 2>/dev/null || true
runc delete victim_container       2>/dev/null || true
runc kill attacker_container KILL  2>/dev/null || true
runc delete attacker_container     2>/dev/null || true

tc qdisc del dev veth-vic-br clsact 2>/dev/null || true
tc qdisc del dev veth-att-br clsact 2>/dev/null || true
rm -f /sys/fs/bpf/tc/globals/flow_map /sys/fs/bpf/ip/globals/flow_map 2>/dev/null || true

echo "Sidecarless experiment complete. Results: $RESULTS_DIR"
