#!/usr/bin/env bash
# run_experiment_sidecarless.sh — Multi-Victim eBPF Mesh

set -euo pipefail

FLOOD_ARR=(0 u1000 u500 u200 u100 u50 u20 u10 u5 u2 u1)
FORTIO_QPS=500
FORTIO_CONNS=10
DURATION_SEC=30
WARMUP_SEC=3
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULTS_DIR="results/sidecarless/$TIMESTAMP"

if [ "$EUID" -ne 0 ]; then echo "ERROR: Must be root."; exit 1; fi
mkdir -p "$RESULTS_DIR"

echo "Step 1: Attaching eBPF..."
./attach_ebpf.sh

echo "Step 2: Spawning runc containers..."
runc kill attacker_container KILL 2>/dev/null || true; runc delete attacker_container 2>/dev/null || true
runc run --bundle attacker_bundle -d attacker_container

for i in 1 2 3; do
    runc kill "victim_container_$i" KILL 2>/dev/null || true; runc delete "victim_container_$i" 2>/dev/null || true
    # Prepare distinct bundles to connect to ns_victim1, 2, 3
    rm -rf "victim_bundle_$i"
    cp -r victim_bundle "victim_bundle_$i"
    sed -i "s/ns_victim/ns_victim$i/g" "victim_bundle_$i/config.json"
    runc run --bundle "victim_bundle_$i" -d "victim_container_$i"
done

echo "Waiting for HTTP servers..."
sleep 2

cleanup_trap() {
    pkill -9 -f 'hping3' 2>/dev/null || true
    runc kill attacker_container KILL 2>/dev/null || true; runc delete attacker_container 2>/dev/null || true
    for i in 1 2 3; do
        runc kill "victim_container_$i" KILL 2>/dev/null || true; runc delete "victim_container_$i" 2>/dev/null || true
    done
    for dev in veth-att-br br-mesh veth-vic1-br veth-vic2-br veth-vic3-br; do tc qdisc del dev "$dev" clsact 2>/dev/null || true; done
}
trap cleanup_trap INT TERM

echo "Step 3: Executing test matrix..."
for flood_arg in "${FLOOD_ARR[@]}"; do
    echo "=========================================================="
    echo "Sidecarless eBPF | Flood: ${flood_arg}"
    echo "=========================================================="

    if [ "$flood_arg" != "0" ]; then
        # Blast Victim 1 (10.0.0.10) to create lock contention
        taskset -c 1 ip netns exec ns_attacker hping3 --udp -p 9999 --interval "${flood_arg}" 10.0.0.10 &>/dev/null &
        P1=$!
        taskset -c 1 ip netns exec ns_attacker hping3 --udp -p 9999 --interval "${flood_arg}" 10.0.0.10 &>/dev/null &
        P2=$!
        taskset -c 1 ip netns exec ns_attacker hping3 --udp -p 9999 --interval "${flood_arg}" 10.0.0.10 &>/dev/null &
        P3=$!
        ATTACKER_PID="$P1 $P2 $P3"
        sleep "$WARMUP_SEC"
    fi

    # Measure all 3 victims
    echo "  Running fortio for 3 victims..."
    taskset -c 0 fortio load -c "${FORTIO_CONNS}" -qps "${FORTIO_QPS}" -t "${DURATION_SEC}s" -json "$RESULTS_DIR/fortio_vic1_${flood_arg}.json" http://10.0.0.10:80/ &
    F1=$!
    taskset -c 0 fortio load -c "${FORTIO_CONNS}" -qps "${FORTIO_QPS}" -t "${DURATION_SEC}s" -json "$RESULTS_DIR/fortio_vic2_${flood_arg}.json" http://10.0.0.11:80/ &
    F2=$!
    taskset -c 0 fortio load -c "${FORTIO_CONNS}" -qps "${FORTIO_QPS}" -t "${DURATION_SEC}s" -json "$RESULTS_DIR/fortio_vic3_${flood_arg}.json" http://10.0.0.12:80/ &
    F3=$!

    wait $F1 $F2 $F3

    if [ "$flood_arg" != "0" ]; then
        kill -9 $ATTACKER_PID 2>/dev/null || true
        wait $ATTACKER_PID 2>/dev/null || true
    fi
    sleep 3
done

cleanup_trap
echo "Complete."
