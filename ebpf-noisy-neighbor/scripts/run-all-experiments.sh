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
mapfile -t EXPERIMENT_ROWS < <(python3 "$ROOT_DIR/scripts/run_experiment_matrix.py" "$CONFIG_FILE")

TOTAL_EXPERIMENTS="${#EXPERIMENT_ROWS[@]}"
if [[ "$TOTAL_EXPERIMENTS" -eq 0 ]]; then
  echo "[run-all] No experiments generated from config: $CONFIG_FILE"
  exit 1
fi

progress_bar() {
  local current="$1"
  local total="$2"
  local width=30
  local percent=$(( current * 100 / total ))
  local filled=$(( percent * width / 100 ))
  local empty=$(( width - filled ))
  local done_part
  local todo_part

  done_part="$(printf '%*s' "$filled" '' | tr ' ' '=')"
  todo_part="$(printf '%*s' "$empty" '' | tr ' ' '.')"

  printf '[%s%s] %3d%% (%d/%d)' "$done_part" "$todo_part" "$percent" "$current" "$total"
}

COMPLETED=0
for row in "${EXPERIMENT_ROWS[@]}"; do
  IFS='|' read -r name noise ebpf containers strategy requests method pattern identity iteration failure p99_threshold <<< "$row"
  [[ -z "$name" ]] && continue
  log_file="$LOG_DIR/${name}.txt"

  COMPLETED=$((COMPLETED + 1))

  echo "[run-all]===================================================="
  echo "[run-all] Progress $(progress_bar "$COMPLETED" "$TOTAL_EXPERIMENTS")"
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
