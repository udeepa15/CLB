#!/usr/bin/env bash
# run_workload_05.sh — Workload 05: Heterogeneous Multi-Tenant Mix (N=10)
set -euo pipefail
REPS="${1:-10}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_USER="${SUDO_USER:-$USER}"

if [ "$EUID" -ne 0 ]; then
  echo "ERROR: Must be run as root (e.g. sudo bash $0 10)"
  exit 1
fi

echo "======================================================================="
echo "=== Executing Workload 05: Multi-Tenant Mix (N=$REPS) ==="
echo "======================================================================="

cd "$DIR/05_multi_tenant_mix"
python3 run_matrix.py --reps "$REPS"
chown -R "$TARGET_USER" "$DIR/05_multi_tenant_mix" 2>/dev/null || true
sudo -u "$TARGET_USER" python3 analyze.py

mkdir -p "$DIR/05_multi_tenant_mix/plots"
latest_plot="$(ls -t "$DIR/05_multi_tenant_mix"/results/multi_run_*/plots/*.png 2>/dev/null | head -n 1 || true)"
if [ -n "$latest_plot" ]; then
  cp "$latest_plot" "$DIR/05_multi_tenant_mix/plots/" 2>/dev/null || true
  chown -R "$TARGET_USER" "$DIR/05_multi_tenant_mix/plots" 2>/dev/null || true
fi

echo "=== Workload 05 Completed! Plots saved to: $DIR/05_multi_tenant_mix/plots/multitenant_per_tenant_p99.png ==="
