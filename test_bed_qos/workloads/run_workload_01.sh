#!/usr/bin/env bash
# run_workload_01.sh — Workload 01: Extended 60s Steady-State Control Baseline (N=10)
set -euo pipefail
REPS="${1:-10}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_USER="${SUDO_USER:-$USER}"

if [ "$EUID" -ne 0 ]; then
  echo "ERROR: Must be run as root (e.g. sudo bash $0 10)"
  exit 1
fi

echo "======================================================================="
echo "=== Executing Workload 01: Extended Steady-State Baseline (N=$REPS) ==="
echo "======================================================================="

cd "$DIR/01_steady_state"
python3 run_matrix.py --reps "$REPS"
chown -R "$TARGET_USER" "$DIR/01_steady_state" 2>/dev/null || true
sudo -u "$TARGET_USER" python3 analyze.py

mkdir -p "$DIR/01_steady_state/plots"
latest_plot="$(ls -t "$DIR/01_steady_state"/results/multi_run_*/plots/*.png 2>/dev/null | head -n 1 || true)"
if [ -n "$latest_plot" ]; then
  cp "$latest_plot" "$DIR/01_steady_state/plots/" 2>/dev/null || true
  chown -R "$TARGET_USER" "$DIR/01_steady_state/plots" 2>/dev/null || true
fi

echo "=== Workload 01 Completed! Plots saved to: $DIR/01_steady_state/plots/steady_state_p99.png ==="
