#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="$ROOT_DIR/config.yaml"
LOG_DIR="$ROOT_DIR/results/raw"
NAME_REGEX=""
ONLY_METHODS=""
ONLY_NOISE=""
ONLY_PATTERNS=""
ONLY_FAILURES=""
ONLY_IDENTITIES=""
ONLY_ITERATIONS=""
DRY_RUN="false"
SKIP_FINALIZE="false"

usage() {
  cat <<'EOF'
Usage: run-all-experiments.sh [config.yaml] [options]

Options:
  --config <path>              Matrix/scenario config file.
  --name-regex <regex>         Run experiments with names matching regex.
  --only-methods <csv>         Filter by isolation methods (none,tc,ebpf,adaptive).
  --only-noise <csv>           Filter by noise levels (low,medium,high,extreme).
  --only-patterns <csv>        Filter by traffic patterns.
  --only-failures <csv>        Filter by failure modes.
  --only-identities <csv>      Filter by identity modes (ip,cgroup).
  --only-iterations <csv>      Filter by iteration numbers.
  --dry-run                    Print selected experiments without executing.
  --skip-finalize              Skip finalize_results.py after execution.
  -h, --help                   Show this help.

Examples:
  sudo ./scripts/run-all-experiments.sh --only-methods adaptive --only-noise high
  sudo ./scripts/run-all-experiments.sh ./configs/limited_matrix.yaml --name-regex '.*fspike.*'
EOF
}

contains_csv() {
  local csv="$1"
  local value="$2"
  local item
  [[ -z "$csv" ]] && return 0
  IFS=',' read -r -a items <<< "$csv"
  for item in "${items[@]}"; do
    if [[ "$(echo "$item" | xargs)" == "$value" ]]; then
      return 0
    fi
  done
  return 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG_FILE="$2"
      shift 2
      ;;
    --name-regex)
      NAME_REGEX="$2"
      shift 2
      ;;
    --only-methods)
      ONLY_METHODS="$2"
      shift 2
      ;;
    --only-noise)
      ONLY_NOISE="$2"
      shift 2
      ;;
    --only-patterns)
      ONLY_PATTERNS="$2"
      shift 2
      ;;
    --only-failures)
      ONLY_FAILURES="$2"
      shift 2
      ;;
    --only-identities)
      ONLY_IDENTITIES="$2"
      shift 2
      ;;
    --only-iterations)
      ONLY_ITERATIONS="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    --skip-finalize)
      SKIP_FINALIZE="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      echo "[run-all] Unknown option: $1"
      usage
      exit 1
      ;;
    *)
      if [[ "$CONFIG_FILE" == "$ROOT_DIR/config.yaml" ]]; then
        CONFIG_FILE="$1"
        shift
      else
        echo "[run-all] Unexpected positional argument: $1"
        usage
        exit 1
      fi
      ;;
  esac
done

if [[ ${EUID} -ne 0 ]]; then
  echo "[run-all] Please run as root (sudo)."
  exit 1
fi

mkdir -p "$LOG_DIR"

echo "[run-all] Using config: $CONFIG_FILE"
mapfile -t EXPERIMENT_ROWS < <(python3 "$ROOT_DIR/scripts/run_experiment_matrix.py" "$CONFIG_FILE")

GENERATED_EXPERIMENTS="${#EXPERIMENT_ROWS[@]}"
if [[ "$GENERATED_EXPERIMENTS" -eq 0 ]]; then
  echo "[run-all] No experiments generated from config: $CONFIG_FILE"
  exit 1
fi

FILTERED_ROWS=()
for row in "${EXPERIMENT_ROWS[@]}"; do
  IFS='|' read -r name noise ebpf containers strategy requests method pattern identity iteration failure p99_threshold tenant_cpu_quota tenant_memory_mb tenant_cpuset noisy_cpu_quota noisy_memory_mb noisy_cpuset host_reserved_cpus host_mem_pressure_mb io_read_bps io_write_bps <<< "$row"
  [[ -z "$name" ]] && continue

  if [[ -n "$NAME_REGEX" && ! "$name" =~ $NAME_REGEX ]]; then
    continue
  fi
  contains_csv "$ONLY_METHODS" "$method" || continue
  contains_csv "$ONLY_NOISE" "$noise" || continue
  contains_csv "$ONLY_PATTERNS" "$pattern" || continue
  contains_csv "$ONLY_FAILURES" "$failure" || continue
  contains_csv "$ONLY_IDENTITIES" "$identity" || continue
  contains_csv "$ONLY_ITERATIONS" "$iteration" || continue

  FILTERED_ROWS+=("$row")
done

TOTAL_EXPERIMENTS="${#FILTERED_ROWS[@]}"
if [[ "$TOTAL_EXPERIMENTS" -eq 0 ]]; then
  echo "[run-all] No experiments matched filters. generated=$GENERATED_EXPERIMENTS"
  exit 1
fi

echo "[run-all] Selected experiments: $TOTAL_EXPERIMENTS / $GENERATED_EXPERIMENTS"

if [[ "$DRY_RUN" == "true" ]]; then
  echo "[run-all] Dry-run mode. Matching experiment names:"
  for row in "${FILTERED_ROWS[@]}"; do
    IFS='|' read -r name _ <<< "$row"
    echo "  - $name"
  done
  exit 0
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
for row in "${FILTERED_ROWS[@]}"; do
  IFS='|' read -r name noise ebpf containers strategy requests method pattern identity iteration failure p99_threshold tenant_cpu_quota tenant_memory_mb tenant_cpuset noisy_cpu_quota noisy_memory_mb noisy_cpuset host_reserved_cpus host_mem_pressure_mb io_read_bps io_write_bps <<< "$row"
  [[ -z "$name" ]] && continue
  log_file="$LOG_DIR/${name}.txt"

  COMPLETED=$((COMPLETED + 1))

  echo "[run-all]===================================================="
  echo "[run-all] Progress $(progress_bar "$COMPLETED" "$TOTAL_EXPERIMENTS")"
  echo "[run-all] experiment=$name noise=$noise pattern=$pattern method=$method ebpf=$ebpf identity=$identity iteration=$iteration failure=$failure containers=$containers strategy=$strategy requests=$requests threshold_ms=$p99_threshold"
  echo "[run-all] resources tenant_cpu=${tenant_cpu_quota}% tenant_mem=${tenant_memory_mb}MB tenant_cpuset=${tenant_cpuset} noisy_cpu=${noisy_cpu_quota}% noisy_mem=${noisy_memory_mb}MB noisy_cpuset=${noisy_cpuset} host_reserved_cpus=${host_reserved_cpus:-none} host_mem_pressure_mb=${host_mem_pressure_mb} io_read_bps=${io_read_bps} io_write_bps=${io_write_bps}"
  echo "[run-all] log=$log_file"

  {
    echo "=== $(date --iso-8601=seconds) START $name ==="
    bash "$ROOT_DIR/scripts/run-single-experiment.sh" \
      "$name" "$noise" "$ebpf" "$containers" "$strategy" "$requests" \
      "$method" "$pattern" "$identity" "$iteration" "$failure" "$p99_threshold" \
      "$tenant_cpu_quota" "$tenant_memory_mb" "$tenant_cpuset" \
      "$noisy_cpu_quota" "$noisy_memory_mb" "$noisy_cpuset" \
      "$host_reserved_cpus" "$host_mem_pressure_mb" "$io_read_bps" "$io_write_bps"
    echo "=== $(date --iso-8601=seconds) END $name ==="
  } | tee "$log_file"
done

if [[ "$SKIP_FINALIZE" == "false" ]]; then
  python3 "$ROOT_DIR/core/finalize_results.py" --root "$ROOT_DIR" --config "$CONFIG_FILE"
else
  echo "[run-all] Skipping finalization by request."
fi

echo "[run-all] All experiments completed."
