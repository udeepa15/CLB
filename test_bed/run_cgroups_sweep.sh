#!/usr/bin/env bash
# run_cgroups_sweep.sh — Cgroups Quota Sweep at peak lock contention (u20)

set -euo pipefail

LIMIT_ARR=(80 85 90 95 99)
FLOOD_ARG="u20"
FORTIO_QPS=50
FORTIO_CONNS=2
DURATION_SEC=10
WARMUP_SEC=2
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULTS_DIR="results/cgroups/$TIMESTAMP"

if [ "$EUID" -ne 0 ]; then echo "ERROR: Must be root."; exit 1; fi
mkdir -p "$RESULTS_DIR"

echo "Step 1: Attaching eBPF..."
./attach_ebpf.sh

cleanup_trap() {
    pkill -9 -f 'hping3' 2>/dev/null || true
    runc kill attacker_container KILL 2>/dev/null || true; runc delete attacker_container 2>/dev/null || true
    for i in 1 2 3; do
        runc kill "victim_container_$i" KILL 2>/dev/null || true; runc delete "victim_container_$i" 2>/dev/null || true
    done
    for dev in veth-att-br br-mesh veth-vic1-br veth-vic2-br veth-vic3-br; do tc qdisc del dev "$dev" clsact 2>/dev/null || true; done
}
trap cleanup_trap INT TERM

for limit in "${LIMIT_ARR[@]}"; do
    echo "=========================================================="
    echo "Cgroups Limit Sweep | Quota: ${limit}% | Flood: ${FLOOD_ARG}"
    echo "=========================================================="
    
    quota=$((limit * 1000))
    period=100000

    echo "Spawning runc containers with CPU limit ${limit}%..."
    runc kill attacker_container KILL 2>/dev/null || true; runc delete attacker_container 2>/dev/null || true
    rm -rf attacker_bundle_limit
    cp -r attacker_bundle attacker_bundle_limit
    jq ".linux.resources.cpu.quota = $quota | .linux.resources.cpu.period = $period" attacker_bundle_limit/config.json > attacker_bundle_limit/config.json.tmp
    mv attacker_bundle_limit/config.json.tmp attacker_bundle_limit/config.json
    runc run --bundle attacker_bundle_limit -d attacker_container

    for i in 1 2 3; do
        runc kill "victim_container_$i" KILL 2>/dev/null || true; runc delete "victim_container_$i" 2>/dev/null || true
        rm -rf "victim_bundle_$i"
        cp -r victim_bundle "victim_bundle_$i"
        sed -i "s/ns_victim/ns_victim$i/g" "victim_bundle_$i/config.json"
        jq ".linux.resources.cpu.cpus = \"$i\" | .linux.resources.cpu.quota = $quota | .linux.resources.cpu.period = $period" "victim_bundle_$i/config.json" > "victim_bundle_$i/config.json.tmp"
        mv "victim_bundle_$i/config.json.tmp" "victim_bundle_$i/config.json"
        runc run --bundle "victim_bundle_$i" -d "victim_container_$i"
    done

    echo "Waiting for HTTP servers to start..."
    sleep 10

    # Blast Victim 1 (10.0.0.10) to create lock contention
    taskset -c 4 ip netns exec ns_attacker hping3 --udp -p 9999 --interval "${FLOOD_ARG}" 10.0.0.10 &>/dev/null &
    P1=$!
    taskset -c 5 ip netns exec ns_attacker hping3 --udp -p 9999 --interval "${FLOOD_ARG}" 10.0.0.10 &>/dev/null &
    P2=$!
    taskset -c 6 ip netns exec ns_attacker hping3 --udp -p 9999 --interval "${FLOOD_ARG}" 10.0.0.10 &>/dev/null &
    P3=$!
    taskset -c 7 ip netns exec ns_attacker hping3 --udp -p 9999 --interval "${FLOOD_ARG}" 10.0.0.10 &>/dev/null &
    P4=$!
    ATTACKER_PID="$P1 $P2 $P3 $P4"
    sleep "$WARMUP_SEC"

    echo "  Running fortio for 3 victims..."
    taskset -c 0 fortio load -c "${FORTIO_CONNS}" -qps "${FORTIO_QPS}" -t "${DURATION_SEC}s" -json "$RESULTS_DIR/fortio_vic1_limit_${limit}.json" http://10.0.0.10:80/ &
    F1=$!
    taskset -c 0 fortio load -c "${FORTIO_CONNS}" -qps "${FORTIO_QPS}" -t "${DURATION_SEC}s" -json "$RESULTS_DIR/fortio_vic2_limit_${limit}.json" http://10.0.0.11:80/ &
    F2=$!
    taskset -c 0 fortio load -c "${FORTIO_CONNS}" -qps "${FORTIO_QPS}" -t "${DURATION_SEC}s" -json "$RESULTS_DIR/fortio_vic3_limit_${limit}.json" http://10.0.0.12:80/ &
    F3=$!

    wait $F1 $F2 $F3

    kill -9 $ATTACKER_PID 2>/dev/null || true
    wait $ATTACKER_PID 2>/dev/null || true
    sleep 3
done

cleanup_trap
echo "Complete."
