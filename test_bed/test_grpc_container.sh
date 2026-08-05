#!/usr/bin/env bash
# test_grpc_container.sh: Verify gRPC fortio server inside victim container.

set -euo pipefail

echo "Setting up topology..."
./setup_topology.sh >/dev/null 2>&1

echo "Spawning Victim container on 10.0.0.10:8079..."
runc kill victim_container_1 KILL 2>/dev/null || true
runc delete victim_container_1 2>/dev/null || true
rm -rf victim_bundle_1 && cp -r victim_bundle victim_bundle_1
sed -i 's/ns_victim/ns_victim1/g' victim_bundle_1/config.json

jq '.process.args = ["sh", "-c", "exec python3 /victim_server.py grpc 8079 >/dev/null 2>&1"]' victim_bundle_1/config.json > victim_bundle_1/config.json.tmp
mv victim_bundle_1/config.json.tmp victim_bundle_1/config.json

runc run --bundle victim_bundle_1 -d victim_container_1 >/dev/null 2>&1
sleep 3

echo "Testing gRPC ping with Fortio..."
fortio load -grpc -ping -c 2 -qps 50 -t 3s 10.0.0.10:8079

echo "Cleaning up..."
runc kill victim_container_1 KILL 2>/dev/null || true
runc delete victim_container_1 2>/dev/null || true
