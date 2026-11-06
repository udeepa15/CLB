#!/usr/bin/env bash
# run_workload_08.sh — Workload 08: Attacker Regime Change Traffic (N=10)
set -euo pipefail
REPS="${1:-10}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_USER="${SUDO_USER:-$USER}"

if [ "$EUID" -ne 0 ]; then
  echo "ERROR: Must be run as root (e.g. sudo bash $0 10)"
  exit 1
fi

echo "======================================================================="
echo "=== Executing Workload 08: Attacker Regime Change (N=$REPS) ==="
echo "======================================================================="

cd "$DIR/08_regime_change"
python3 run_matrix.py --reps "$REPS"
chown -R "$TARGET_USER" "$DIR/08_regime_change" 2>/dev/null || true
sudo -u "$TARGET_USER" python3 analyze.py

mkdir -p "$DIR/08_regime_change/plots"
latest_plot="$(ls -t "$DIR/08_regime_change"/results/multi_run_*/plots/*.png 2>/dev/null | head -n 1 || true)"
if [ -n "$latest_plot" ]; then
  cp "$latest_plot" "$DIR/08_regime_change/plots/" 2>/dev/null || true
  chown -R "$TARGET_USER" "$DIR/08_regime_change/plots" 2>/dev/null || true
fi

echo "=== Workload 08 Completed! Plots saved to: $DIR/08_regime_change/plots/regime_change_p99.png ==="
