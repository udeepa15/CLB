#!/usr/bin/env bash
# run_markov_matrix.sh — Execution Runner for qos_markov Across Workloads 01..06
set -euo pipefail

REPS="${1:-10}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_USER="${SUDO_USER:-$USER}"

if [ "$EUID" -ne 0 ]; then
  echo "ERROR: Must be run as root (e.g. sudo bash $0 10)"
  exit 1
fi

echo "========================================================================="
echo "=== Executing qos_markov Predictive Benchmark Suite (N=$REPS Reps) ==="
echo "========================================================================="

mkdir -p "$DIR/results"
python3 "$DIR/run_markov_runner.py" --reps "$REPS"

chown -R "$TARGET_USER" "$DIR/results" 2>/dev/null || true

echo "=== qos_markov Matrix Benchmark Run Complete! ==="
echo "Results saved in: $DIR/results/"
