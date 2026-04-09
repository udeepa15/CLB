#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 21 ]]; then
        echo "Usage: $0 <experiment_name> <noise_level> <ebpf_enabled> <strategy> <containers> <isolation_method> <traffic_pattern> <identity_mode> <iteration> <failure_mode> <p99_threshold_ms> <tenant_cpu_quota_pct> <tenant_memory_mb> <tenant_cpuset> <noisy_cpu_quota_pct> <noisy_memory_mb> <noisy_cpuset> <host_reserved_cpus> <host_mem_pressure_mb> <io_read_bps> <io_write_bps> [hook_point]"
  exit 1
fi

EXPERIMENT_NAME="$1"
NOISE_LEVEL="$2"
EBPF_ENABLED="$3"
STRATEGY="$4"
CONTAINERS="$5"
ISOLATION_METHOD="$6"
TRAFFIC_PATTERN="$7"
IDENTITY_MODE="$8"
ITERATION="$9"
FAILURE_MODE="${10}"
P99_THRESHOLD_MS="${11}"
TENANT_CPU_QUOTA_PCT="${12}"
TENANT_MEMORY_MB="${13}"
TENANT_CPUSET="${14}"
NOISY_CPU_QUOTA_PCT="${15}"
NOISY_MEMORY_MB="${16}"
NOISY_CPUSET="${17}"
HOST_RESERVED_CPUS="${18}"
HOST_MEM_PRESSURE_MB="${19}"
IO_READ_BPS="${20}"
IO_WRITE_BPS="${21}"
HOOK_POINT="${22:-${HOOK_POINT:-tc}}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW_DIR="$ROOT_DIR/results/raw"
PROC_DIR="$ROOT_DIR/results/processed"
SUMMARY_CSV="$PROC_DIR/summary.csv"
RESULTS_CSV="$PROC_DIR/results.csv"
RAW_RESULTS_CSV="$RAW_DIR/results.csv"
DIST_CSV="$PROC_DIR/latency_distribution.csv"
INVENTORY_FILE="$ROOT_DIR/containers/runtime/inventory.csv"
KERNEL_PROFILE_CSV="$RAW_DIR/${EXPERIMENT_NAME}_kernel_contention.csv"
MAP_LOOKUP_CSV="$RAW_DIR/${EXPERIMENT_NAME}_map_lookup_latency.csv"

mkdir -p "$RAW_DIR" "$PROC_DIR"

if [[ ! -f "$SUMMARY_CSV" ]]; then
  echo "timestamp,scenario,tenant,requests,failures,p99_ms" > "$SUMMARY_CSV"
fi

RESULTS_HEADER="timestamp,experiment,noise_level,traffic_pattern,isolation_method,identity_mode,ebpf_enabled,strategy,hook_point,containers,iteration,failure_mode,tenant,tenant_cpu_quota_pct,tenant_memory_mb,tenant_cpuset,noisy_cpu_quota_pct,noisy_memory_mb,noisy_cpuset,host_reserved_cpus,host_mem_pressure_mb,io_read_bps,io_write_bps,requests,failures,p50_ms,p95_ms,p99_ms,p999_ms,p9999_ms,jitter_ms,variance_ms2,throughput_rps,packet_drops,tail_amplification,recovery_window_samples,degradation_window_samples,map_lookup_latency_us,kernel_ksoftirqd_latency_us,kernel_ebpf_prog_id,kernel_ebpf_prog_name,kernel_ebpf_prog_type,kernel_ebpf_prog_time_ns_per_run,cg_cpu_usage_usec,cg_cpu_throttled_usec,cg_memory_current_bytes,cg_memory_events_oom,cg_io_read_bytes,cg_io_write_bytes,control_plane_convergence_ms,isolation_score"

if [[ ! -f "$RESULTS_CSV" ]]; then
    echo "$RESULTS_HEADER" > "$RESULTS_CSV"
fi

if [[ ! -f "$RAW_RESULTS_CSV" ]]; then
    echo "$RESULTS_HEADER" > "$RAW_RESULTS_CSV"
fi

if [[ ! -f "$DIST_CSV" ]]; then
  echo "experiment,tenant,bucket_ms,count" > "$DIST_CSV"
fi

packet_drops="0"
if [[ "$EBPF_ENABLED" == "true" ]]; then
  packet_drops="$("$ROOT_DIR/ebpf/tc/attach.sh" drops "$STRATEGY" 2>/dev/null || echo 0)"
  packet_drops="${packet_drops:-0}"
fi

bash "$ROOT_DIR/scripts/kernel-contention-profiler.sh" "$EXPERIMENT_NAME" 3 "$HOOK_POINT" >/dev/null 2>&1 || true

parse_field() {
  local line="$1"
  local key="$2"
  echo "$line" | sed -n "s/.*${key}=\([^ ]*\).*/\1/p"
}

read_csv_field_by_tenant() {
    local csv_file="$1"
    local tenant="$2"
    local col_idx="$3"
    [[ -f "$csv_file" ]] || {
        echo "na"
        return
    }
    awk -F, -v t="$tenant" -v c="$col_idx" 'NR>1 && $2==t {print $c; exit}' "$csv_file" 2>/dev/null | tail -n1 | sed '/^$/d' || true
}

get_cgroup_path() {
    local container_name="$1"
    awk -F, -v n="$container_name" '$1==n {print $2}' "$ROOT_DIR/containers/runtime/cgroup_ids.csv" | tail -n1
}

read_cg_metric() {
    local cgroup_path="$1"
    local rel_file="$2"
    local key="$3"
    local full_path="/sys/fs/cgroup${cgroup_path}/${rel_file}"

    [[ -f "$full_path" ]] || {
        echo "na"
        return
    }

    if [[ -z "$key" ]]; then
        head -n1 "$full_path" 2>/dev/null || echo "na"
        return
    fi

    awk -v k="$key" '
        {
            for (i = 1; i <= NF; i++) {
                split($i, p, "=")
                if (p[1] == k && p[2] != "") {
                    print p[2]
                    exit
                }
            }
            if ($1 == k && $2 != "") {
                print $2
                exit
            }
        }
    ' "$full_path" 2>/dev/null | tail -n1 | sed '/^$/d' || true
}

while IFS=, read -r name role ip; do
  [[ "$name" == "name" ]] && continue
  [[ "$role" != "tenant" ]] && continue

  log_file="$RAW_DIR/${EXPERIMENT_NAME}_${name}.log"
  summary_line="$(grep '^SUMMARY ' "$log_file" | tail -n1 || true)"
  if [[ -z "$summary_line" ]]; then
    echo "[collect-metrics] WARNING: missing SUMMARY for ${EXPERIMENT_NAME}/${name}"
    continue
  fi

  requests="$(parse_field "$summary_line" requests)"
  failures="$(parse_field "$summary_line" failures)"
  p50="$(parse_field "$summary_line" p50_ms)"
  p95="$(parse_field "$summary_line" p95_ms)"
  p99="$(parse_field "$summary_line" p99_ms)"
    p999="$(parse_field "$summary_line" p999_ms)"
    p9999="$(parse_field "$summary_line" p9999_ms)"
  jitter="$(parse_field "$summary_line" jitter_ms)"
  variance="$(parse_field "$summary_line" variance_ms2)"
  throughput="$(parse_field "$summary_line" throughput_rps)"

  [[ -z "$variance" ]] && variance="nan"
    [[ -z "$p999" ]] && p999="nan"
    [[ -z "$p9999" ]] && p9999="nan"

  tail_amp="nan"
  if [[ -n "$p50" && "$p50" != "0" && "$p50" != "nan" && "$p99" != "nan" ]]; then
    tail_amp="$(python3 - <<PY
p50=float("$p50")
p99=float("$p99")
print(f"{(p99/p50):.4f}" if p50 > 0 else "nan")
PY
)"
  fi

  recovery_window="na"
  degradation_window="na"
    cg_cpu_usage_usec="na"
    cg_cpu_throttled_usec="na"
    cg_memory_current_bytes="na"
    cg_memory_events_oom="na"
    cg_io_read_bytes="na"
    cg_io_write_bytes="na"
  ts_file="$RAW_DIR/${EXPERIMENT_NAME}_${name}.timeseries.csv"
  if [[ -f "$ts_file" ]]; then
    windows="$(python3 - "$ts_file" "$P99_THRESHOLD_MS" <<'PY'
import csv
import sys

ts_file = sys.argv[1]
thr = float(sys.argv[2])

series = []
with open(ts_file, 'r', encoding='utf-8') as f:
    r = csv.DictReader(f)
    for row in r:
        try:
            series.append(float(row['latency_ms']))
        except Exception:
            continue

if not series:
    print("na,na")
    raise SystemExit(0)

worst_deg = 0
cur = 0
for v in series:
    if v > thr:
        cur += 1
        worst_deg = max(worst_deg, cur)
    else:
        cur = 0

recover = 0
for i in range(len(series) - 1, -1, -1):
    if series[i] <= thr:
        recover += 1
    else:
        break

print(f"{recover},{worst_deg}")
PY
)"
    recovery_window="${windows%%,*}"
    degradation_window="${windows##*,}"
  fi

    cg_path="$(get_cgroup_path "$name")"
    if [[ -n "$cg_path" ]]; then
        cg_cpu_usage_usec="$(read_cg_metric "$cg_path" cpu.stat usage_usec)"
        cg_cpu_throttled_usec="$(read_cg_metric "$cg_path" cpu.stat throttled_usec)"
        cg_memory_current_bytes="$(read_cg_metric "$cg_path" memory.current "")"
        cg_memory_events_oom="$(read_cg_metric "$cg_path" memory.events oom)"
        cg_io_read_bytes="$(read_cg_metric "$cg_path" io.stat rbytes)"
        cg_io_write_bytes="$(read_cg_metric "$cg_path" io.stat wbytes)"
        [[ -z "$cg_cpu_usage_usec" ]] && cg_cpu_usage_usec="na"
        [[ -z "$cg_cpu_throttled_usec" ]] && cg_cpu_throttled_usec="na"
        [[ -z "$cg_memory_current_bytes" ]] && cg_memory_current_bytes="na"
        [[ -z "$cg_memory_events_oom" ]] && cg_memory_events_oom="na"
        [[ -z "$cg_io_read_bytes" ]] && cg_io_read_bytes="na"
        [[ -z "$cg_io_write_bytes" ]] && cg_io_write_bytes="na"
    fi

  ts="$(date --iso-8601=seconds)"
  scenario="baseline"
  if [[ "$EBPF_ENABLED" == "true" ]]; then
    scenario="ebpf"
  fi

    map_lookup_latency_us="$(read_csv_field_by_tenant "$MAP_LOOKUP_CSV" "$name" 3)"
    [[ -z "$map_lookup_latency_us" ]] && map_lookup_latency_us="na"

    kernel_ksoftirqd_latency_us="$(read_csv_field_by_tenant "$KERNEL_PROFILE_CSV" "$name" 4)"
    kernel_ebpf_prog_id="$(read_csv_field_by_tenant "$KERNEL_PROFILE_CSV" "$name" 5)"
    kernel_ebpf_prog_name="$(read_csv_field_by_tenant "$KERNEL_PROFILE_CSV" "$name" 6)"
    kernel_ebpf_prog_type="$(read_csv_field_by_tenant "$KERNEL_PROFILE_CSV" "$name" 7)"
    kernel_ebpf_prog_time_ns_per_run="$(read_csv_field_by_tenant "$KERNEL_PROFILE_CSV" "$name" 8)"
    [[ -z "$kernel_ksoftirqd_latency_us" ]] && kernel_ksoftirqd_latency_us="na"
    [[ -z "$kernel_ebpf_prog_id" ]] && kernel_ebpf_prog_id="na"
    [[ -z "$kernel_ebpf_prog_name" ]] && kernel_ebpf_prog_name="na"
    [[ -z "$kernel_ebpf_prog_type" ]] && kernel_ebpf_prog_type="na"
    [[ -z "$kernel_ebpf_prog_time_ns_per_run" ]] && kernel_ebpf_prog_time_ns_per_run="na"

    control_plane_convergence_ms="${CONTROL_PLANE_CONVERGENCE_MS:-na}"

  echo "$ts,$scenario,$name,$requests,$failures,$p99" >> "$SUMMARY_CSV"
    row="$ts,$EXPERIMENT_NAME,$NOISE_LEVEL,$TRAFFIC_PATTERN,$ISOLATION_METHOD,$IDENTITY_MODE,$EBPF_ENABLED,$STRATEGY,$HOOK_POINT,$CONTAINERS,$ITERATION,$FAILURE_MODE,$name,$TENANT_CPU_QUOTA_PCT,$TENANT_MEMORY_MB,$TENANT_CPUSET,$NOISY_CPU_QUOTA_PCT,$NOISY_MEMORY_MB,$NOISY_CPUSET,${HOST_RESERVED_CPUS:-none},$HOST_MEM_PRESSURE_MB,$IO_READ_BPS,$IO_WRITE_BPS,$requests,$failures,$p50,$p95,$p99,$p999,$p9999,$jitter,$variance,$throughput,$packet_drops,$tail_amp,$recovery_window,$degradation_window,$map_lookup_latency_us,$kernel_ksoftirqd_latency_us,$kernel_ebpf_prog_id,$kernel_ebpf_prog_name,$kernel_ebpf_prog_type,$kernel_ebpf_prog_time_ns_per_run,$cg_cpu_usage_usec,$cg_cpu_throttled_usec,$cg_memory_current_bytes,$cg_memory_events_oom,$cg_io_read_bytes,$cg_io_write_bytes,$control_plane_convergence_ms,na"
    echo "$row" >> "$RESULTS_CSV"
    echo "$row" >> "$RAW_RESULTS_CSV"
  echo "[collect-metrics] $EXPERIMENT_NAME/$name p99=$p99 jitter=$jitter throughput=$throughput"

  lat_file="$RAW_DIR/${EXPERIMENT_NAME}_${name}.latency"
  if [[ -f "$lat_file" ]]; then
    python3 - "$lat_file" "$EXPERIMENT_NAME" "$name" >> "$DIST_CSV" <<'PY'
import sys

path, experiment, tenant = sys.argv[1:4]
buckets = [0, 1, 2, 5, 10, 20, 50, 100, 200]
counts = {b: 0 for b in buckets}
counts["200+"] = 0

with open(path, 'r', encoding='utf-8') as f:
    for line in f:
        if line.startswith('#'):
            continue
        parts = line.strip().rsplit(',', 1)
        if len(parts) != 2:
            continue
        try:
            v = float(parts[1])
        except Exception:
            continue
        placed = False
        for b in buckets:
            if v <= b:
                counts[b] += 1
                placed = True
                break
        if not placed:
            counts["200+"] += 1

for b in buckets:
    print(f"{experiment},{tenant},{b},{counts[b]}")
print(f"{experiment},{tenant},200+,{counts['200+']}")
PY
  fi
done < "$INVENTORY_FILE"

python3 - "$RESULTS_CSV" "$RAW_RESULTS_CSV" <<'PY'
import csv
from pathlib import Path

def update_file(path: Path) -> None:
    if not path.exists():
        return

    rows = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            rows.append(row)

    baseline = {}
    for r in rows:
        if r.get("isolation_method") == "none" and r.get("noise_level") == "low":
            try:
                baseline[r["tenant"]] = float(r["p99_ms"])
            except Exception:
                pass

    for r in rows:
        tenant = r.get("tenant")
        try:
            p99 = float(r.get("p99_ms", "nan"))
        except Exception:
            r["isolation_score"] = "na"
            continue

        b = baseline.get(tenant)
        if b is None or b <= 0:
            r["isolation_score"] = "na"
        else:
            r["isolation_score"] = f"{p99 / b:.4f}"

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


for p in __import__('sys').argv[1:]:
    update_file(Path(p))
PY

echo "[collect-metrics] Updated: $SUMMARY_CSV"
echo "[collect-metrics] Updated: $RESULTS_CSV"
echo "[collect-metrics] Updated: $RAW_RESULTS_CSV"
