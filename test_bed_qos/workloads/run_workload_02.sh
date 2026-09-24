#!/usr/bin/env bash
# run_workload_02.sh — Workload 02: Bursty Oscillating Attacker Traffic (N=10)
set -euo pipefail
REPS="${1:-10}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_USER="${SUDO_USER:-$USER}"

if [ "$EUID" -ne 0 ]; then
  echo "ERROR: Must be run as root (e.g. sudo bash $0 10)"
  exit 1
fi

echo "======================================================================="
echo "=== Executing Workload 02: Bursty Oscillating Attacker (N=$REPS) ==="
echo "======================================================================="

cd "$DIR/02_bursty_oscillating"
python3 run_matrix.py --reps "$REPS"
chown -R "$TARGET_USER" "$DIR/02_bursty_oscillating" 2>/dev/null || true
sudo -u "$TARGET_USER" python3 analyze.py

mkdir -p "$DIR/02_bursty_oscillating/plots"
latest_plot="$(ls -t "$DIR/02_bursty_oscillating"/results/multi_run_*/plots/*.png 2>/dev/null | head -n 1 || true)"
if [ -n "$latest_plot" ]; then
  cp "$latest_plot" "$DIR/02_bursty_oscillating/plots/" 2>/dev/null || true
  chown -R "$TARGET_USER" "$DIR/02_bursty_oscillating/plots" 2>/dev/null || true
fi

echo "=== Workload 02 Completed! Plots saved to: $DIR/02_bursty_oscillating/plots/bursty_p99_box.png ==="
