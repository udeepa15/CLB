#!/usr/bin/env bash
# run_workload_10.sh - Workload 10: many-tenant benchmark (default 100 containers: 95 victims + 5 attackers)
#
#   sudo bash run_workload_10.sh calibrate        # 1) qos_open sweep -> calibration/<tenants>.json
#   sudo bash run_workload_10.sh run [REPS]       # 2) arms x scenarios matrix (default REPS=10)
#
# Env: TENANTS_FILE (default tenants/scale100.json), ARMS, SCENARIOS, DURATION (s, default 30), QPS (default 20).
set -euo pipefail
MODE="${1:-run}"
REPS="${2:-10}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="$(dirname "$DIR")"
TARGET_USER="${SUDO_USER:-$USER}"
export TENANTS_FILE="${TENANTS_FILE:-tenants/scale100.json}"
export PYTHONDONTWRITEBYTECODE=1
ARMS="${ARMS:-sidecarless qos_open qos_static qos_dynamic_fixed qos_markov qos_auto_port}"
SCENARIOS="${SCENARIOS:-none a1_flood a5_flood a5_small}"
DURATION="${DURATION:-30}"
QPS="${QPS:-20}"

if [ "$EUID" -ne 0 ]; then echo "ERROR: must be run as root (sudo bash $0 ...)"; exit 1; fi
cd "$BASE"
[ -f "$TENANTS_FILE" ] || python3 tenants.py gen --victims 95 --attackers 5 --cpus 1-7 --out "$TENANTS_FILE"
python3 smoke_test.py --arms qos_static qos_auto_port qos_open    # refuse to run on a broken data plane

cd "$DIR/10_scale"
if [ "$MODE" = "calibrate" ]; then
    python3 run_matrix.py --sweep --reps 3 --architectures qos_open --duration "$DURATION" --qps "$QPS"
    RUN="$(ls -td results/sweep_* | head -n1)"
    chown -R "$TARGET_USER" "$DIR/10_scale" "$BASE/calibration" 2>/dev/null || true
    sudo -u "$TARGET_USER" -E python3 "$BASE/calibrate.py" "$RUN"
else
    # shellcheck disable=SC2086
    python3 run_matrix.py --reps "$REPS" --architectures $ARMS --scenarios $SCENARIOS --duration "$DURATION" --qps "$QPS"
    chown -R "$TARGET_USER" "$DIR/10_scale" 2>/dev/null || true
    sudo -u "$TARGET_USER" -E python3 analyze.py
fi
echo "=== Workload 10 ($MODE) done ==="
