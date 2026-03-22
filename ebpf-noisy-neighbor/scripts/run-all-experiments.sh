#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${1:-$ROOT_DIR/config.yaml}"
LOG_DIR="$ROOT_DIR/results/raw"

if [[ ${EUID} -ne 0 ]]; then
  echo "[run-all] Please run as root (sudo)."
  exit 1
fi

mkdir -p "$LOG_DIR"

echo "[run-all] Using config: $CONFIG_FILE"
python3 "$ROOT_DIR/scripts/run_experiment_matrix.py" "$CONFIG_FILE" | while IFS='|' read -r name noise ebpf containers strategy requests method pattern identity iteration failure p99_threshold; do
  [[ -z "$name" ]] && continue
  log_file="$LOG_DIR/${name}.txt"

  echo "[run-all]===================================================="
  echo "[run-all] experiment=$name noise=$noise pattern=$pattern method=$method ebpf=$ebpf identity=$identity iteration=$iteration failure=$failure containers=$containers strategy=$strategy requests=$requests threshold_ms=$p99_threshold"
  echo "[run-all] log=$log_file"

  {
    echo "=== $(date --iso-8601=seconds) START $name ==="
    "$ROOT_DIR/scripts/run-single-experiment.sh" \
      "$name" "$noise" "$ebpf" "$containers" "$strategy" "$requests" \
      "$method" "$pattern" "$identity" "$iteration" "$failure" "$p99_threshold"
    echo "=== $(date --iso-8601=seconds) END $name ==="
  } | tee "$log_file"
done

python3 "$ROOT_DIR/core/finalize_results.py" --root "$ROOT_DIR" --config "$CONFIG_FILE"

echo "[run-all] All experiments completed."
