#!/usr/bin/env bash
# run_manual_baseline.sh: Single manual baseline run (no flood) for HTTP and UDP.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"

RESULTS_DIR="results/manual_baseline"
mkdir -p "$RESULTS_DIR"

if [ "$EUID" -ne 0 ]; then echo "ERROR: Must be root."; exit 1; fi

echo "=== Step 1: Setting up network topology & eBPF ==="
./setup_topology.sh
./attach_ebpf.sh

echo "=== Step 2: Running HTTP baseline against Victim 1 ==="
runc kill victim_container_1 KILL 2>/dev/null || true
runc delete victim_container_1 2>/dev/null || true
rm -rf victim_bundle_1 && cp -r victim_bundle victim_bundle_1
sed -i 's/ns_victim/ns_victim1/g' victim_bundle_1/config.json
jq '.linux.resources.cpu.cpus = "1"' victim_bundle_1/config.json > victim_bundle_1/config.json.tmp
mv victim_bundle_1/config.json.tmp victim_bundle_1/config.json

# Set HTTP server
jq '.process.args = ["sh", "-c", "exec python3 /victim_server.py http 80 >/dev/null 2>&1"]' victim_bundle_1/config.json > victim_bundle_1/config.json.tmp
mv victim_bundle_1/config.json.tmp victim_bundle_1/config.json

runc run --bundle victim_bundle_1 -d victim_container_1 >/dev/null 2>&1
sleep 3

taskset -c 0 fortio load -c "${FORTIO_CONNS}" -qps "${FORTIO_QPS}" -t "${DURATION_SEC}s" -json "$RESULTS_DIR/http_baseline.json" http://10.0.0.10:80/

echo "=== Step 3: Running UDP baseline against Victim 1 ==="
runc kill victim_container_1 KILL 2>/dev/null || true
runc delete victim_container_1 2>/dev/null || true

# Set UDP server
jq '.process.args = ["sh", "-c", "exec python3 /victim_server.py udp 8078 >/dev/null 2>&1"]' victim_bundle_1/config.json > victim_bundle_1/config.json.tmp
mv victim_bundle_1/config.json.tmp victim_bundle_1/config.json

runc run --bundle victim_bundle_1 -d victim_container_1 >/dev/null 2>&1
sleep 3

taskset -c 0 fortio load -c "${FORTIO_CONNS}" -qps "${FORTIO_QPS}" -t "${DURATION_SEC}s" -json "$RESULTS_DIR/udp_baseline.json" udp://10.0.0.10:8078

echo "=== Step 4: Cleanup ==="
runc kill victim_container_1 KILL 2>/dev/null || true
runc delete victim_container_1 2>/dev/null || true

echo "Manual baseline run complete. Results saved in $RESULTS_DIR"
