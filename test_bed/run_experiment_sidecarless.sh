#!/usr/bin/env bash
# run_experiment_sidecarless.sh  —  Isolation Deficit demonstration (eBPF mesh).
#
# DESIGN RATIONALE:
#   The attacker runs hping3 --flood from the HOST network namespace, sending
#   UDP packets to 10.0.0.10 (victim IP). Because hping3 runs on the host,
#   every flood packet egresses through veth-vic-br, hitting the eBPF
#   shared_global_key classifier and acquiring the same htab bucket spinlock
#   that fortio's measurement packets must also acquire.
#
#   Under heavy flood (high PPS), the spinlock hold time and queue depth grow,
#   causing measurable p99 tail-latency spikes in the victim's HTTP responses —
#   the "Isolation Deficit" that a shared-state eBPF mesh exposes.
#
# Traffic flow:
#   hping3 (host) --flood--> veth-vic-br [eBPF spinlock] --> ns_victim
#   fortio  (host) qps=5000-> veth-vic-br [same spinlock] --> ns_victim HTTP
#
# Load levels: hping3 flood rate controlled by --interval (i<N> = N microseconds).
# FLOOD_ARR values are hping3 --interval arguments:
#   "u100"  ~10,000 pps   (light)
#   "u50"   ~20,000 pps   (medium)
#   "u10"   ~100,000 pps  (heavy)
#   "u1"    ~1,000,000 pps (maximum)
#   "flood" = kernel-max pps (extreme)

set -euo pipefail

# ── Tuning knobs ──────────────────────────────────────────────────────────────
CPU_CORE_SET="0"

# hping3 --interval values -> progressively heavier PPS flood
# "0" entry = baseline (no attacker)
FLOOD_ARR=(0 u100 u50 u10 u1)

# Victim measurement QPS — must be high enough to reliably hit the spinlock
FORTIO_QPS=5000
FORTIO_CONNS=50

DURATION_SEC=30
WARMUP_SEC=3
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULTS_DIR="results/sidecarless/$TIMESTAMP"

# ── Sanity checks ─────────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Must be run as root." >&2; exit 1
fi

for cmd in runc fortio bpftrace clang tc conntrack hping3 taskset; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: Missing required host tool: '$cmd'." >&2; exit 1
    fi
done

mkdir -p "$RESULTS_DIR"

# ── Step 1: Remove sidecar residue ───────────────────────────────────────────
echo "Step 1: Cleaning up sidecar proxies and NAT rules..."
pkill -9 -f "socat TCP-LISTEN:8080" 2>/dev/null || true
ip netns exec ns_victim iptables -t nat -F 2>/dev/null || true

# ── Step 2: Attach eBPF shared-key contention classifier ─────────────────────
echo "Step 2: Compiling and attaching eBPF classifier (shared_global_key)..."
./attach_ebpf.sh

# ── Step 3: Start runc containers ────────────────────────────────────────────
echo "Step 3: Spawning runc OCI containers..."
runc kill victim_container   KILL 2>/dev/null || true; runc delete victim_container   2>/dev/null || true
runc kill attacker_container KILL 2>/dev/null || true; runc delete attacker_container 2>/dev/null || true

runc run --bundle victim_bundle   -d victim_container
runc run --bundle attacker_bundle -d attacker_container
ip netns exec ns_victim ip link set dev lo up

echo "Waiting for Python HTTP server (port 80) inside victim..."
for i in {1..40}; do
    ip netns exec ns_victim nc -z 127.0.0.1 80 2>/dev/null && { echo "HTTP server up."; break; }
    [ "$i" -eq 40 ] && { echo "ERROR: HTTP server never started." >&2; exit 1; }
    sleep 0.5
done

# ── Cleanup trap ─────────────────────────────────────────────────────────────
cleanup_trap() {
    echo "Aborting! Cleaning up..."
    pkill -9 -f 'hping3' 2>/dev/null || true
    runc kill victim_container   KILL 2>/dev/null || true; runc delete victim_container   2>/dev/null || true
    runc kill attacker_container KILL 2>/dev/null || true; runc delete attacker_container 2>/dev/null || true
    tc qdisc del dev veth-vic-br clsact 2>/dev/null || true
    tc qdisc del dev veth-att-br clsact 2>/dev/null || true
    rm -f /sys/fs/bpf/tc/globals/flow_map /sys/fs/bpf/ip/globals/flow_map 2>/dev/null || true
}
trap cleanup_trap INT TERM

# ── Step 4: Test matrix ───────────────────────────────────────────────────────
echo "Step 4: Executing test matrix..."
for flood_arg in "${FLOOD_ARR[@]}"; do
    echo "=========================================================="
    echo "Sidecarless eBPF | Attacker hping3 flood: ${flood_arg}"
    echo "=========================================================="

    ATTACKER_PID=""
    FLOOD_DUR=$((DURATION_SEC + WARMUP_SEC + 5))

    # 4a. Launch HOST-SIDE hping3 UDP flood -> 10.0.0.10 (egresses through veth-vic-br)
    if [ "$flood_arg" != "0" ]; then
        echo "  Launching hping3 --udp --interval ${flood_arg} --flood to 10.0.0.10 (CPU core ${CPU_CORE_SET})..."
        # hping3 runs on HOST -> packets egress veth-vic-br -> eBPF fires -> ns_victim
        taskset -c "$CPU_CORE_SET" hping3 --udp -p 9999             --interval "${flood_arg}" 10.0.0.10 &>/dev/null &
        ATTACKER_PID=$!
        sleep "$WARMUP_SEC"
    fi

    # 4b. bpftrace: eBPF map latency + spinlock slowpath contention
    echo "  Starting bpftrace eBPF contention monitor..."
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

    # 4c. Fortio: measure victim HTTP at high QPS (competes with flood on same veth)
    echo "  Running fortio at ${FORTIO_QPS} QPS, ${FORTIO_CONNS} connections for ${DURATION_SEC}s..."
    taskset -c "$CPU_CORE_SET" fortio load         -c "${FORTIO_CONNS}" -qps "${FORTIO_QPS}" -t "${DURATION_SEC}s"         -json "$RESULTS_DIR/fortio_load_${flood_arg}.json"         http://10.0.0.10:80/

    # 4d. Tear down this iteration
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
pkill -9 -f 'hping3' 2>/dev/null || true
runc kill victim_container   KILL 2>/dev/null || true; runc delete victim_container   2>/dev/null || true
runc kill attacker_container KILL 2>/dev/null || true; runc delete attacker_container 2>/dev/null || true
tc qdisc del dev veth-vic-br clsact 2>/dev/null || true
tc qdisc del dev veth-att-br clsact 2>/dev/null || true
rm -f /sys/fs/bpf/tc/globals/flow_map /sys/fs/bpf/ip/globals/flow_map 2>/dev/null || true

echo "Sidecarless experiment complete. Results: $RESULTS_DIR"
