#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${1:-$ROOT_DIR/configs/limited_matrix.yaml}"

if [[ $# -gt 0 && "$1" != --* ]]; then
  CONFIG_FILE="$1"
  shift
fi

if [[ ${EUID} -ne 0 ]]; then
  echo "[run-limited] Please run as root (sudo)."
  exit 1
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "[run-limited] Config not found: $CONFIG_FILE"
  exit 1
fi

TOTAL_EXPERIMENTS="$(python3 "$ROOT_DIR/scripts/run_experiment_matrix.py" "$CONFIG_FILE" | wc -l | tr -d ' ')"

echo "[run-limited] Using config: $CONFIG_FILE"
echo "[run-limited] Planned experiments: $TOTAL_EXPERIMENTS"

echo "[run-limited] Delegating to run-all orchestrator..."
"$ROOT_DIR/scripts/run-all-experiments.sh" "$CONFIG_FILE" "$@"
