#!/usr/bin/env bash
# run_cap_sweep.sh: show that no single STATIC cap fits all load conditions.
# Runs the static arms (tc police, tc tbf, tiered, percpu_static) over several caps for one workload.
# Usage: sudo ./run_cap_sweep.sh <workload_number 1-6> [reps=10] [caps_in_bytes_per_sec...]
# The cap of every run is recorded in that run's env.json (arms[].static_cap_bps).
set -euo pipefail
cd "$(dirname "$0")"
WL="${1:?usage: run_cap_sweep.sh <workload 1-6> [reps] [caps...]}"; REPS="${2:-10}"; shift $(( $# >= 2 ? 2 : $# ))
CAPS=("$@"); [ ${#CAPS[@]} -eq 0 ] && CAPS=(625000 2500000 5000000 12500000 50000000)  # 5/20/40/100/400 Mbit/s
DIR=$(ls -d workloads/0${WL}_* | head -1)
for cap in "${CAPS[@]}"; do
  echo "=== static cap ${cap} B/s ==="
  TIER_L2_RATE_BPS="$cap" python3 "$DIR/run_matrix.py" --reps "$REPS" \
      --architectures qos_tc_police qos_tc_tbf qos_tiered qos_percpu_static
done
