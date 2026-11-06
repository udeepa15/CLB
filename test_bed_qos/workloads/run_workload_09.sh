#!/usr/bin/env bash
# run_workload_09.sh — Workload 09: Automatic Attacker Identification Traffic (N=10)
set -euo pipefail
REPS="${1:-10}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_USER="${SUDO_USER:-$USER}"

if [ "$EUID" -ne 0 ]; then
  echo "ERROR: Must be run as root (e.g. sudo bash $0 10)"
  exit 1
fi

echo "======================================================================="
echo "=== Executing Workload 09: Automatic Attacker Identification (N=$REPS) ==="
echo "======================================================================="

cd "$DIR/09_auto_identification"
python3 run_matrix.py --reps "$REPS"
chown -R "$TARGET_USER" "$DIR/09_auto_identification" 2>/dev/null || true
sudo -u "$TARGET_USER" python3 analyze.py

mkdir -p "$DIR/09_auto_identification/plots"
latest_plot="$(ls -t "$DIR/09_auto_identification"/results/multi_run_*/plots/*.png 2>/dev/null | head -n 1 || true)"
if [ -n "$latest_plot" ]; then
  cp "$latest_plot" "$DIR/09_auto_identification/plots/" 2>/dev/null || true
  chown -R "$TARGET_USER" "$DIR/09_auto_identification/plots" 2>/dev/null || true
fi

echo "=== Workload 09 Completed! Plots saved to: $DIR/09_auto_identification/plots/auto_identification_p99.png ==="
