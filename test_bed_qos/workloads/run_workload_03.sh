#!/usr/bin/env bash
# run_workload_03.sh — Workload 03: Ramping Escalation Attacker Traffic (N=10)
set -euo pipefail
REPS="${1:-10}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_USER="${SUDO_USER:-$USER}"

if [ "$EUID" -ne 0 ]; then
  echo "ERROR: Must be run as root (e.g. sudo bash $0 10)"
  exit 1
fi

echo "======================================================================="
echo "=== Executing Workload 03: Ramping Escalation Attacker (N=$REPS) ==="
echo "======================================================================="

cd "$DIR/03_ramping_escalation"
python3 run_matrix.py --reps "$REPS"
chown -R "$TARGET_USER" "$DIR/03_ramping_escalation" 2>/dev/null || true
sudo -u "$TARGET_USER" python3 analyze.py

mkdir -p "$DIR/03_ramping_escalation/plots"
latest_plot="$(ls -t "$DIR/03_ramping_escalation"/results/multi_run_*/plots/*.png 2>/dev/null | head -n 1 || true)"
if [ -n "$latest_plot" ]; then
  cp "$latest_plot" "$DIR/03_ramping_escalation/plots/" 2>/dev/null || true
  chown -R "$TARGET_USER" "$DIR/03_ramping_escalation/plots" 2>/dev/null || true
fi

echo "=== Workload 03 Completed! Plots saved to: $DIR/03_ramping_escalation/plots/ramping_p99_bar.png ==="
