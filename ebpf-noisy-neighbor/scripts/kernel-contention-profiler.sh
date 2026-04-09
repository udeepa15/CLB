#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW_DIR="$ROOT_DIR/results/raw"
CG_FILE="$ROOT_DIR/containers/runtime/cgroup_ids.csv"

EXPERIMENT_NAME="${1:?experiment name required}"
DURATION_S="${2:-3}"
HOOK_POINT="${3:-auto}"

mkdir -p "$RAW_DIR"

OUT_CSV="$RAW_DIR/${EXPERIMENT_NAME}_kernel_contention.csv"
BPFTXT="$RAW_DIR/${EXPERIMENT_NAME}_bpftool_profile.txt"
PERF_DATA="$RAW_DIR/${EXPERIMENT_NAME}_perf_sched.data"
PERF_TXT="$RAW_DIR/${EXPERIMENT_NAME}_perf_sched_latency.txt"

if [[ ! -f "$CG_FILE" ]]; then
  echo "[kernel-profiler] Missing cgroup id file: $CG_FILE"
  exit 0
fi

ksoftirqd_latency_us="na"
if command -v perf >/dev/null 2>&1; then
  if perf sched record -a -o "$PERF_DATA" -- sleep "$DURATION_S" >/dev/null 2>&1; then
    perf sched latency -i "$PERF_DATA" > "$PERF_TXT" 2>/dev/null || true
    ksoftirqd_latency_us="$(python3 - "$PERF_TXT" <<'PY'
import re
import sys
from pathlib import Path

p = Path(sys.argv[1])
if not p.exists():
    print("na")
    raise SystemExit(0)

vals = []
for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
    if "ksoftirqd" not in ln:
        continue
    nums = re.findall(r"[-+]?\d*\.?\d+", ln)
    for n in nums:
        try:
            vals.append(float(n))
        except Exception:
            pass
if not vals:
    print("na")
else:
    # perf sched latency often reports milliseconds; convert to microseconds.
    print(f"{max(vals) * 1000.0:.3f}")
PY
)"
  fi
fi

prog_rows="$(python3 - "$HOOK_POINT" <<'PY'
import json
import subprocess
import sys

hook = sys.argv[1]
cp = subprocess.run(["bpftool", "-j", "prog", "show"], text=True, capture_output=True)
if cp.returncode != 0:
    raise SystemExit(0)

try:
    rows = json.loads(cp.stdout)
except Exception:
    raise SystemExit(0)

wanted_types = {"sched_cls", "xdp"}
for r in rows:
    typ = str(r.get("type", ""))
    if typ not in wanted_types:
        continue
    if hook != "auto" and hook != typ:
        continue
    name = str(r.get("name", ""))
    if name in {"noisy_dropper", "noisy_rate_limit", "adaptive_classifier", "xdp_noisy_dropper", "xdp_noisy_rate_limit"}:
        print(f"{r.get('id','')}|{name}|{typ}")
PY
)"

: > "$BPFTXT"
prog_time_ns_per_run="na"
prog_id="na"
prog_name="na"
prog_type="na"

if [[ -n "$prog_rows" ]]; then
  while IFS='|' read -r id name typ; do
    [[ -z "$id" ]] && continue
    echo "=== prog_id=$id name=$name type=$typ ===" >> "$BPFTXT"
    profile_out="$(bpftool prog profile id "$id" duration "$DURATION_S" 2>/dev/null || true)"
    if [[ -n "$profile_out" ]]; then
      echo "$profile_out" >> "$BPFTXT"
    fi

    candidate="$(python3 - <<'PY'
import re, sys
text = sys.stdin.read()
run_cnt = None
run_time = None
for pat in [r"run_cnt\D+(\d+)", r"run_cnt:\s*(\d+)"]:
    m = re.search(pat, text)
    if m:
        run_cnt = int(m.group(1))
        break
for pat in [r"run_time_ns\D+(\d+)", r"run_time_ns:\s*(\d+)"]:
    m = re.search(pat, text)
    if m:
        run_time = int(m.group(1))
        break
if run_cnt and run_time and run_cnt > 0:
    print(f"{run_time/run_cnt:.3f}")
PY
<<<"$profile_out")"

    if [[ -n "$candidate" ]]; then
      prog_time_ns_per_run="$candidate"
      prog_id="$id"
      prog_name="$name"
      prog_type="$typ"
      break
    fi
  done <<< "$prog_rows"
fi

{
  echo "experiment,tenant,cgroup_id,ksoftirqd_latency_us,ebpf_prog_id,ebpf_prog_name,ebpf_prog_type,ebpf_prog_time_ns_per_run,hook_point,timestamp"
  while IFS=, read -r name _cg_path cgroup_id; do
    [[ "$name" == "name" ]] && continue
    [[ -z "$name" ]] && continue
    echo "$EXPERIMENT_NAME,$name,${cgroup_id:-na},$ksoftirqd_latency_us,$prog_id,$prog_name,$prog_type,$prog_time_ns_per_run,${HOOK_POINT:-auto},$(date --iso-8601=seconds)"
  done < "$CG_FILE"
} > "$OUT_CSV"

echo "[kernel-profiler] Wrote: $OUT_CSV"
