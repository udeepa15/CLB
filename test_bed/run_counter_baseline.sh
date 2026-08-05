#!/usr/bin/env bash
# run_counter_baseline.sh: Manual baseline run with eBPF hits/sec counter poller.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"

RESULTS_DIR="results/manual_counter"
mkdir -p "$RESULTS_DIR"

if [ "$EUID" -ne 0 ]; then echo "ERROR: Must be root."; exit 1; fi

echo "=== Step 1: Setting up topology & eBPF ==="
./setup_topology.sh >/dev/null 2>&1
./attach_ebpf.sh >/dev/null 2>&1

# ── HTTP Counter Baseline ─────────────────────────────────────────────────────
echo "=== Step 2: Running HTTP baseline (QPS=50, conns=2, 10s) with eBPF counter poller ==="
runc kill victim_container_1 KILL 2>/dev/null || true
runc delete victim_container_1 2>/dev/null || true
rm -rf victim_bundle_1 && cp -r victim_bundle victim_bundle_1
sed -i 's/ns_victim/ns_victim1/g' victim_bundle_1/config.json
jq '.linux.resources.cpu.cpus = "1"' victim_bundle_1/config.json > victim_bundle_1/config.json.tmp
mv victim_bundle_1/config.json.tmp victim_bundle_1/config.json
jq '.process.args = ["sh", "-c", "exec python3 /victim_server.py http 80 >/dev/null 2>&1"]' victim_bundle_1/config.json > victim_bundle_1/config.json.tmp
mv victim_bundle_1/config.json.tmp victim_bundle_1/config.json

runc run --bundle victim_bundle_1 -d victim_container_1 >/dev/null 2>&1
sleep 3

# Start eBPF stats collector in background
python3 collect_ebpf_stats.py "$RESULTS_DIR/http_ebpf_stats.jsonl" &
POLLER_PID=$!

taskset -c 0 fortio load -c "${FORTIO_CONNS}" -qps "${FORTIO_QPS}" -t "${DURATION_SEC}s" -json "$RESULTS_DIR/http_baseline.json" http://10.0.0.10:80/

kill -9 $POLLER_PID 2>/dev/null || true

# ── UDP Counter Baseline ──────────────────────────────────────────────────────
echo "=== Step 3: Running UDP baseline (QPS=50, conns=2, 10s) with eBPF counter poller ==="
runc kill victim_container_1 KILL 2>/dev/null || true
runc delete victim_container_1 2>/dev/null || true

jq '.process.args = ["sh", "-c", "exec python3 /victim_server.py udp 8078 >/dev/null 2>&1"]' victim_bundle_1/config.json > victim_bundle_1/config.json.tmp
mv victim_bundle_1/config.json.tmp victim_bundle_1/config.json

runc run --bundle victim_bundle_1 -d victim_container_1 >/dev/null 2>&1
sleep 3

python3 collect_ebpf_stats.py "$RESULTS_DIR/udp_ebpf_stats.jsonl" &
POLLER_PID=$!

taskset -c 0 fortio load -c "${FORTIO_CONNS}" -qps "${FORTIO_QPS}" -t "${DURATION_SEC}s" -json "$RESULTS_DIR/udp_baseline.json" udp://10.0.0.10:8078

kill -9 $POLLER_PID 2>/dev/null || true

echo "=== Step 4: Teardown ==="
runc kill victim_container_1 KILL 2>/dev/null || true
runc delete victim_container_1 2>/dev/null || true

echo "Counter baseline run complete. Results saved in $RESULTS_DIR"
