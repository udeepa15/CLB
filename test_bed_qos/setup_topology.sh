#!/usr/bin/env bash
# setup_topology.sh [cleanup] - build (or remove) the tenant network.
# Thin wrapper around topology.py, which reads the tenants file ($TENANTS_FILE, default
# tenants/default.json = the original 3 victims + 1 attacker on 10.0.0.0/24).
set -euo pipefail
if [ "$EUID" -ne 0 ]; then echo "ERROR: Must be root."; exit 1; fi
cd "$(dirname "$0")"
if [ "${1:-}" = "cleanup" ]; then exec python3 topology.py down; fi
exec python3 topology.py up
