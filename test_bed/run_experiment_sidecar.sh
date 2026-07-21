#!/usr/bin/env bash
# run_experiment_sidecar.sh — Multi-Victim Sidecar Proxy Baseline

set -euo pipefail

FLOOD_ARR=(0 u1000 u500 u200 u100 u50 u20 u10 u5 u2 u1)
FORTIO_QPS=500
FORTIO_CONNS=10
DURATION_SEC=30
WARMUP_SEC=3
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULTS_DIR="results/sidecar/$TIMESTAMP"

if [ "$EUID" -ne 0 ]; then echo "ERROR: Must be root."; exit 1; fi
mkdir -p "$RESULTS_DIR"

echo "Step 1: Detaching eBPF..."
pkill -9 -f "socat TCP-LISTEN:8080" 2>/dev/null || true
for dev in veth-att-br br-mesh veth-vic1-br veth-vic2-br veth-vic3-br; do tc qdisc del dev "$dev" clsact 2>/dev/null || true; done
rm -f /sys/fs/bpf/tc/globals/flow_map /sys/fs/bpf/ip/globals/flow_map 2>/dev/null || true

echo "Step 2: Spawning runc containers..."
runc kill attacker_container KILL 2>/dev/null || true; runc delete attacker_container 2>/dev/null || true
runc run --bundle attacker_bundle -d attacker_container

for i in 1 2 3; do
    runc kill "victim_container_$i" KILL 2>/dev/null || true; runc delete "victim_container_$i" 2>/dev/null || true
    rm -rf "victim_bundle_$i"
    cp -r victim_bundle "victim_bundle_$i"
    sed -i "s/ns_victim/ns_victim$i/g" "victim_bundle_$i/config.json"
    jq ".linux.resources.cpu.cpus = \"$i\"" "victim_bundle_$i/config.json" > "victim_bundle_$i/config.json.tmp"
    mv "victim_bundle_$i/config.json.tmp" "victim_bundle_$i/config.json"
    runc run --bundle "victim_bundle_$i" -d "victim_container_$i"
done
sleep 2

echo "Step 3: Configuring NAT and Sidecars for all 3 victims..."
for i in 1 2 3; do
    ip netns exec "ns_victim$i" iptables -t nat -F PREROUTING 2>/dev/null || true
    ip netns exec "ns_victim$i" iptables -t nat -A PREROUTING -i "veth-victim$i" -p tcp --dport 80 -j REDIRECT --to-ports 8080
    ip netns exec "ns_victim$i" taskset -c 0 socat TCP-LISTEN:8080,fork,reuseaddr,retry=5 TCP:127.0.0.1:80 &
done
sleep 1

cleanup_trap() {
    pkill -9 -f 'socat' 2>/dev/null || true
    pkill -9 -f 'hping3' 2>/dev/null || true
    runc kill attacker_container KILL 2>/dev/null || true; runc delete attacker_container 2>/dev/null || true
    for i in 1 2 3; do
        runc kill "victim_container_$i" KILL 2>/dev/null || true; runc delete "victim_container_$i" 2>/dev/null || true
        ip netns exec "ns_victim$i" iptables -t nat -F 2>/dev/null || true
    done
}
trap cleanup_trap INT TERM

echo "Step 4: Executing test matrix..."
for flood_arg in "${FLOOD_ARR[@]}"; do
    echo "=========================================================="
    echo "Sidecar Proxy | Flood: ${flood_arg}"
    echo "=========================================================="

    if [ "$flood_arg" != "0" ]; then
        taskset -c 4 ip netns exec ns_attacker hping3 --udp -p 9999 --interval "${flood_arg}" 10.0.0.10 &>/dev/null &
        P1=$!
        taskset -c 5 ip netns exec ns_attacker hping3 --udp -p 9999 --interval "${flood_arg}" 10.0.0.10 &>/dev/null &
        P2=$!
        taskset -c 6 ip netns exec ns_attacker hping3 --udp -p 9999 --interval "${flood_arg}" 10.0.0.10 &>/dev/null &
        P3=$!
        ATTACKER_PID="$P1 $P2 $P3"
        sleep "$WARMUP_SEC"
    fi

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
