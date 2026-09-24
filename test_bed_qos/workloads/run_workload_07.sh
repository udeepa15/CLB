#!/usr/bin/env bash
# run_workload_07.sh — Workload 07: Rational Adaptive Adversary 3-Arm Ablation (N=10)
set -euo pipefail
REPS="${1:-10}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_USER="${SUDO_USER:-$USER}"

if [ "$EUID" -ne 0 ]; then
  echo "ERROR: Must be run as root (e.g. sudo bash $0 10)"
  exit 1
fi

echo "======================================================================="
echo "=== Executing Workload 07: Rational Adaptive Adversary (N=$REPS) ==="
echo "======================================================================="

cd "$DIR/07_adaptive_attacker"
python3 run_matrix.py --reps "$REPS"
chown -R "$TARGET_USER" "$DIR/07_adaptive_attacker" 2>/dev/null || true
sudo -u "$TARGET_USER" python3 analyze.py

mkdir -p "$DIR/07_adaptive_attacker/plots"
latest_plot="$(ls -t "$DIR/07_adaptive_attacker"/results/multi_run_*/plots/*.png 2>/dev/null | head -n 1 || true)"
if [ -n "$latest_plot" ]; then
  cp "$latest_plot" "$DIR/07_adaptive_attacker/plots/" 2>/dev/null || true
  chown -R "$TARGET_USER" "$DIR/07_adaptive_attacker/plots" 2>/dev/null || true
fi

echo "=== Workload 07 Completed! Plots saved to: $DIR/07_adaptive_attacker/plots/adaptive_3arm_p99.png ==="
