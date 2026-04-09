#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TS="$(date +%Y%m%d_%H%M%S)"
STRATEGY="${1:-rate_limit}"
NOISE_LEVEL="${NOISE_LEVEL:-high}"
REQUESTS="${REQUESTS:-400}"
CONTAINERS="${CONTAINERS:-3}"

if [[ ${EUID} -ne 0 ]]; then
  echo "[hook-benchmark] Please run as root (sudo)."
  exit 1
fi

if [[ "$STRATEGY" != "dropper" && "$STRATEGY" != "rate_limit" ]]; then
  echo "[hook-benchmark] Strategy must be dropper or rate_limit for XDP benchmark."
  exit 1
fi

run_case() {
  local hook="$1"
  local exp="hookcmp_${hook}_${STRATEGY}_${TS}"
  echo "[hook-benchmark] Running $exp"
  HOOK_POINT="$hook" "$ROOT_DIR/scripts/run-single-experiment.sh" \
    "$exp" "$NOISE_LEVEL" true "$CONTAINERS" "$STRATEGY" "$REQUESTS" \
    ebpf constant ip 1 none 8.0 \
    80 512 auto 60 384 auto "" 0 0 0 "$hook"
}

run_case tc
run_case xdp

python3 - "$ROOT_DIR/results/processed/results.csv" "$TS" <<'PY'
import csv
import math
import sys
from collections import defaultdict

path = sys.argv[1]
ts = sys.argv[2]
agg = defaultdict(list)
with open(path, "r", encoding="utf-8") as f:
    r = csv.DictReader(f)
    for row in r:
        exp = row.get("experiment", "")
        if f"_{ts}" not in exp or not exp.startswith("hookcmp_"):
            continue
        hook = row.get("hook_point", "tc")
        try:
            p99 = float(row.get("p99_ms", "nan"))
        except Exception:
            continue
        if math.isfinite(p99):
            agg[hook].append(p99)

print("[hook-benchmark] P99 summary (lower is better):")
for hook in sorted(agg):
    vals = agg[hook]
    if not vals:
        continue
    print(f"  hook={hook} samples={len(vals)} avg_p99_ms={sum(vals)/len(vals):.3f}")
PY

echo "[hook-benchmark] Done."
