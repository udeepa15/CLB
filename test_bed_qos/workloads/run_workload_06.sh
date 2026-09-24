#!/usr/bin/env bash
# run_workload_06.sh — Workload 06: Unbounded Retry Storm Amplification (N=10)
set -euo pipefail
REPS="${1:-10}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_USER="${SUDO_USER:-$USER}"

if [ "$EUID" -ne 0 ]; then
  echo "ERROR: Must be run as root (e.g. sudo bash $0 10)"
  exit 1
fi

echo "======================================================================="
echo "=== Executing Workload 06: Retry Storm Amplification (N=$REPS) ==="
echo "======================================================================="

cd "$DIR/06_retry_storm"
python3 run_matrix.py --reps "$REPS"
chown -R "$TARGET_USER" "$DIR/06_retry_storm" 2>/dev/null || true
sudo -u "$TARGET_USER" python3 analyze.py

mkdir -p "$DIR/06_retry_storm/plots"
latest_plot="$(ls -t "$DIR/06_retry_storm"/results/multi_run_*/plots/*.png 2>/dev/null | head -n 1 || true)"
if [ -n "$latest_plot" ]; then
  cp "$latest_plot" "$DIR/06_retry_storm/plots/" 2>/dev/null || true
  chown -R "$TARGET_USER" "$DIR/06_retry_storm/plots" 2>/dev/null || true
fi

echo "=== Workload 06 Completed! Plots saved to: $DIR/06_retry_storm/plots/retry_storm_amplification.png ==="
