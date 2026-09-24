#!/usr/bin/env bash
# run_workload_04.sh — Workload 04: Flash Crowd Victim Demand Surge (N=10)
set -euo pipefail
REPS="${1:-10}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_USER="${SUDO_USER:-$USER}"

if [ "$EUID" -ne 0 ]; then
  echo "ERROR: Must be run as root (e.g. sudo bash $0 10)"
  exit 1
fi

echo "======================================================================="
echo "=== Executing Workload 04: Flash Crowd Victim Demand Surge (N=$REPS) ==="
echo "======================================================================="

cd "$DIR/04_flash_crowd_victim"
python3 run_matrix.py --reps "$REPS"
chown -R "$TARGET_USER" "$DIR/04_flash_crowd_victim" 2>/dev/null || true
sudo -u "$TARGET_USER" python3 analyze.py

mkdir -p "$DIR/04_flash_crowd_victim/plots"
latest_plot="$(ls -t "$DIR/04_flash_crowd_victim"/results/multi_run_*/plots/*.png 2>/dev/null | head -n 1 || true)"
if [ -n "$latest_plot" ]; then
  cp "$latest_plot" "$DIR/04_flash_crowd_victim/plots/" 2>/dev/null || true
  chown -R "$TARGET_USER" "$DIR/04_flash_crowd_victim/plots" 2>/dev/null || true
fi

echo "=== Workload 04 Completed! Plots saved to: $DIR/04_flash_crowd_victim/plots/flash_crowd_phase_p99.png ==="
