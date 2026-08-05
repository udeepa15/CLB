#!/usr/bin/env bash
# verify_ebpf_udp_counter.sh — Verify that UDP port 8078 traffic triggers eBPF flow_map updates.

set -euo pipefail

if [ "$EUID" -ne 0 ]; then echo "ERROR: Must be root."; exit 1; fi

echo "=== Step 1: Setting up topology and attaching eBPF ==="
./setup_topology.sh >/dev/null 2>&1
./attach_ebpf.sh >/dev/null 2>&1

echo "=== Step 2: Starting UDP Victim Server on 10.0.0.10:8078 ==="
runc kill victim_container_1 KILL 2>/dev/null || true
runc delete victim_container_1 2>/dev/null || true
rm -rf victim_bundle_1 && cp -r victim_bundle victim_bundle_1
sed -i 's/ns_victim/ns_victim1/g' victim_bundle_1/config.json
jq '.linux.resources.cpu.cpus = "1"' victim_bundle_1/config.json > victim_bundle_1/config.json.tmp
mv victim_bundle_1/config.json.tmp victim_bundle_1/config.json
jq '.process.args = ["sh", "-c", "exec python3 /victim_server.py udp 8078 >/dev/null 2>&1"]' victim_bundle_1/config.json > victim_bundle_1/config.json.tmp
mv victim_bundle_1/config.json.tmp victim_bundle_1/config.json

runc run --bundle victim_bundle_1 -d victim_container_1 >/dev/null 2>&1
sleep 3

echo "=== Step 3: Initial bpftool flow_map dump ==="
bpftool map dump name flow_map || true

echo "=== Step 4: Sending 50 UDP packets to port 8078 via Fortio ==="
fortio load -c 1 -n 50 udp://10.0.0.10:8078 >/dev/null 2>&1 || true

echo "=== Step 5: Post-test bpftool flow_map dump ==="
bpftool map dump name flow_map || true

echo "=== Teardown ==="
runc kill victim_container_1 KILL 2>/dev/null || true
runc delete victim_container_1 2>/dev/null || true
