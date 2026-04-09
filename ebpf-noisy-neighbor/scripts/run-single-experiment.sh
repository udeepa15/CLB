#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW_DIR="$ROOT_DIR/results/raw"
RUNC_ROOT="/run/runc-ebpf-noisy-neighbor"

EXP_NAME="${1:?experiment name required}"
NOISE_LEVEL="${2:?noise level required}"
EBPF_ENABLED="${3:?ebpf true/false required}"
CONTAINERS="${4:?container count required}"
STRATEGY="${5:-none}"
REQUESTS="${6:-300}"
ISOLATION_METHOD="${7:-none}"
TRAFFIC_PATTERN="${8:-constant}"
IDENTITY_MODE="${9:-ip}"
ITERATION="${10:-1}"
FAILURE_MODE="${11:-none}"
P99_THRESHOLD_MS="${12:-10.0}"
TENANT_CPU_QUOTA_PCT="${13:-80}"
TENANT_MEMORY_MB="${14:-512}"
TENANT_CPUSET="${15:-auto}"
NOISY_CPU_QUOTA_PCT="${16:-60}"
NOISY_MEMORY_MB="${17:-384}"
NOISY_CPUSET="${18:-auto}"
HOST_RESERVED_CPUS="${19:-}"
HOST_MEM_PRESSURE_MB="${20:-0}"
IO_READ_BPS="${21:-0}"
IO_WRITE_BPS="${22:-0}"

ADAPTIVE_PID=""
ADAPTIVE_STOP="$RAW_DIR/.adaptive_stop"
HOST_PRESSURE_PID=""

if [[ ${EUID} -ne 0 ]]; then
  echo "[run-single] Please run as root (sudo)."
  exit 1
fi

mkdir -p "$RAW_DIR"

wait_http_ready() {
  local url="$1"
  local retries="${2:-80}"

  for ((i=1; i<=retries; i++)); do
    if curl -s --max-time 1 -o /dev/null "$url"; then
      return 0
    fi
    sleep 0.25
  done

  echo "[run-single] ERROR: Timed out waiting for $url"
  return 1
}

run_clients() {
  local inventory="$ROOT_DIR/containers/runtime/inventory.csv"
  while IFS=, read -r name role ip; do
    [[ "$name" == "name" ]] && continue
    [[ "$role" != "tenant" ]] && continue

    local url="http://${ip}:8080/"
    local prefix="$RAW_DIR/${EXP_NAME}_${name}"

    echo "[run-single] Waiting for $name at $url"
    wait_http_ready "$url"

    echo "[run-single] Collecting latency from $name"
    bash "$ROOT_DIR/workloads/client.sh" "$url" "$REQUESTS" "$prefix"
  done < "$inventory"
}

cleanup() {
  touch "$ADAPTIVE_STOP" 2>/dev/null || true
  if [[ -n "${ADAPTIVE_PID}" ]]; then
    kill "$ADAPTIVE_PID" 2>/dev/null || true
    wait "$ADAPTIVE_PID" 2>/dev/null || true
  fi
  if [[ -n "${HOST_PRESSURE_PID}" ]]; then
    kill "$HOST_PRESSURE_PID" 2>/dev/null || true
    wait "$HOST_PRESSURE_PID" 2>/dev/null || true
  fi
  bash "$ROOT_DIR/scripts/stop-containers.sh" || true
}

normalize_int() {
  local raw="$1"
  local fallback="$2"
  local min_val="$3"

  if [[ "$raw" =~ ^[0-9]+$ ]]; then
    if (( raw < min_val )); then
      echo "$min_val"
      return
    fi
    echo "$raw"
    return
  fi

  echo "$fallback"
}

derive_cpuset() {
  local requested="$1"
  local reserved="$2"
  local total_cpus
  total_cpus="$(nproc --all 2>/dev/null || echo 1)"

  if [[ "$requested" != "auto" && -n "$requested" ]]; then
    echo "$requested"
    return
  fi

  # If host-reserved CPUs are set, assign remaining CPUs to containers.
  if [[ -n "$reserved" ]]; then
    local max_cpu=$(( total_cpus - 1 ))
    local all_set="0-${max_cpu}"
    local available
    available="$(python3 - "$all_set" "$reserved" <<'PY'
import sys

def parse_set(s: str) -> set[int]:
    out: set[int] = set()
    for part in s.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            a, b = part.split('-', 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out

def render(vals: list[int]) -> str:
    if not vals:
        return ""
    vals = sorted(vals)
    ranges = []
    start = vals[0]
    prev = vals[0]
    for v in vals[1:]:
        if v == prev + 1:
            prev = v
            continue
        ranges.append(f"{start}-{prev}" if start != prev else f"{start}")
        start = prev = v
    ranges.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ",".join(ranges)

all_set = parse_set(sys.argv[1])
reserved_set = parse_set(sys.argv[2]) if sys.argv[2].strip() else set()
allowed = sorted(v for v in all_set if v not in reserved_set)
print(render(allowed))
PY
)"
    if [[ -n "$available" ]]; then
      echo "$available"
      return
    fi
  fi

  echo "0-$((total_cpus - 1))"
}

apply_limit_to_cgroup() {
  local cgroup_path="$1"
  local cpu_quota_pct="$2"
  local memory_mb="$3"
  local cpuset="$4"
  local read_bps="$5"
  local write_bps="$6"

  local full_path="/sys/fs/cgroup${cgroup_path}"
  [[ -d "$full_path" ]] || return 0

  local cpu_quota_us=$(( cpu_quota_pct * 1000 ))
  local memory_bytes=$(( memory_mb * 1024 * 1024 ))

  if [[ -w "$full_path/cpu.max" ]]; then
    echo "${cpu_quota_us} 100000" > "$full_path/cpu.max" || true
  fi

  if [[ -w "$full_path/memory.max" ]]; then
    echo "$memory_bytes" > "$full_path/memory.max" || true
  fi

  if [[ -n "$cpuset" && -w "$full_path/cpuset.cpus" ]]; then
    echo "$cpuset" > "$full_path/cpuset.cpus" || true
  fi

  if [[ ( "$read_bps" -gt 0 || "$write_bps" -gt 0 ) && -w "$full_path/io.max" ]]; then
    local dev
    dev="$(stat -c '%t:%T' / 2>/dev/null || true)"
    if [[ -n "$dev" ]]; then
      local major minor
      major="$((16#${dev%:*}))"
      minor="$((16#${dev#*:}))"
      {
        if [[ "$read_bps" -gt 0 ]]; then
          echo "$major:$minor rbps=$read_bps"
        fi
        if [[ "$write_bps" -gt 0 ]]; then
          echo "$major:$minor wbps=$write_bps"
        fi
      } > "$full_path/io.max" || true
    fi
  fi
}

apply_resource_allocations() {
  local tenant_cpu
  local tenant_mem
  local noisy_cpu
  local noisy_mem
  local host_pressure_mb
  local io_read
  local io_write
  local tenant_cpuset
  local noisy_cpuset

  tenant_cpu="$(normalize_int "$TENANT_CPU_QUOTA_PCT" 80 1)"
  tenant_mem="$(normalize_int "$TENANT_MEMORY_MB" 512 64)"
  noisy_cpu="$(normalize_int "$NOISY_CPU_QUOTA_PCT" 60 1)"
  noisy_mem="$(normalize_int "$NOISY_MEMORY_MB" 384 64)"
  host_pressure_mb="$(normalize_int "$HOST_MEM_PRESSURE_MB" 0 0)"
  io_read="$(normalize_int "$IO_READ_BPS" 0 0)"
  io_write="$(normalize_int "$IO_WRITE_BPS" 0 0)"
  tenant_cpuset="$(derive_cpuset "$TENANT_CPUSET" "$HOST_RESERVED_CPUS")"
  noisy_cpuset="$(derive_cpuset "$NOISY_CPUSET" "$HOST_RESERVED_CPUS")"

  while IFS=, read -r name role _ip; do
    [[ "$name" == "name" ]] && continue
    local cg_path
    cg_path="$(awk -F, -v n="$name" '$1==n {print $2}' "$ROOT_DIR/containers/runtime/cgroup_ids.csv" | tail -n1)"
    [[ -z "$cg_path" ]] && continue

    if [[ "$role" == "tenant" ]]; then
      apply_limit_to_cgroup "$cg_path" "$tenant_cpu" "$tenant_mem" "$tenant_cpuset" "$io_read" "$io_write"
    else
      apply_limit_to_cgroup "$cg_path" "$noisy_cpu" "$noisy_mem" "$noisy_cpuset" "$io_read" "$io_write"
    fi
  done < "$ROOT_DIR/containers/runtime/inventory.csv"

  if [[ "$host_pressure_mb" -gt 0 ]]; then
    python3 - "$host_pressure_mb" <<'PY' &
import sys
import time

mb = int(sys.argv[1])
buf = bytearray(mb * 1024 * 1024)
stride = 4096
while True:
    for i in range(0, len(buf), stride):
        buf[i] = (buf[i] + 1) % 256
    time.sleep(0.25)
PY
    HOST_PRESSURE_PID="$!"
    echo "[run-single] Host memory pressure enabled at ${host_pressure_mb}MB pid=$HOST_PRESSURE_PID"
  fi

  echo "[run-single] Applied resource limits tenant_cpu=${tenant_cpu}% tenant_mem=${tenant_mem}MB tenant_cpuset=${tenant_cpuset} noisy_cpu=${noisy_cpu}% noisy_mem=${noisy_mem}MB noisy_cpuset=${noisy_cpuset} host_reserved_cpus=${HOST_RESERVED_CPUS:-none} host_mem_pressure_mb=${host_pressure_mb} io_read_bps=${io_read} io_write_bps=${io_write}"
}

write_cgroup_inventory() {
  local out="$ROOT_DIR/containers/runtime/cgroup_ids.csv"
  echo "name,cgroup_path,cgroup_id" > "$out"

  while IFS=, read -r name role ip; do
    [[ "$name" == "name" ]] && continue
    local pid
    pid="$(runc --root "$RUNC_ROOT" state "$name" 2>/dev/null | jq -r '.pid')"
    [[ -z "$pid" || "$pid" == "null" ]] && continue

    local cg_path
    cg_path="$(awk -F: '$1=="0" {print $3}' "/proc/${pid}/cgroup" 2>/dev/null || true)"
    [[ -z "$cg_path" ]] && cg_path="/"

    local full_path="/sys/fs/cgroup${cg_path}"
    local cg_id="0"
    if [[ -e "$full_path" ]]; then
      cg_id="$(stat -Lc '%i' "$full_path" 2>/dev/null || echo 0)"
    fi

    echo "$name,$cg_path,$cg_id" >> "$out"
  done < "$ROOT_DIR/containers/runtime/inventory.csv"

  echo "[run-single] Wrote cgroup inventory: $out"
}

apply_tc_baseline() {
  local iface="veth-noisy"
  local rate="80mbit"

  case "$NOISE_LEVEL" in
    low) rate="200mbit" ;;
    medium) rate="80mbit" ;;
    high) rate="30mbit" ;;
    extreme) rate="10mbit" ;;
  esac

  tc qdisc del dev "$iface" root 2>/dev/null || true
  tc qdisc add dev "$iface" root handle 1: htb default 10
  tc class add dev "$iface" parent 1: classid 1:10 htb rate "$rate" ceil "$rate"
  tc qdisc add dev "$iface" parent 1:10 fq_codel || true

  echo "[run-single] Applied tc baseline shaping on $iface rate=$rate"
}

clear_tc_baseline() {
  tc qdisc del dev "veth-noisy" root 2>/dev/null || true
}

start_adaptive_controller() {
  local latency_file="$RAW_DIR/${EXP_NAME}_tenant1.latency"
  local log_file="$RAW_DIR/${EXP_NAME}_adaptive_controller.log"

  rm -f "$ADAPTIVE_STOP"

  python3 "$ROOT_DIR/core/adaptive_controller.py" \
    --root "$ROOT_DIR" \
    --latency-file "$latency_file" \
    --p99-threshold-ms "$P99_THRESHOLD_MS" \
    --identity-mode "$IDENTITY_MODE" \
    --sample-interval 1.0 \
    --window-size 120 \
    --log-file "$log_file" &

  ADAPTIVE_PID="$!"
  echo "[run-single] Adaptive controller started pid=$ADAPTIVE_PID"
}

apply_isolation() {
  case "$ISOLATION_METHOD" in
    none)
      clear_tc_baseline
      bash "$ROOT_DIR/ebpf/tc/attach.sh" detach || true
      ;;
    tc)
      clear_tc_baseline
      bash "$ROOT_DIR/ebpf/tc/attach.sh" detach || true
      apply_tc_baseline
      ;;
    ebpf)
      clear_tc_baseline
      bash "$ROOT_DIR/ebpf/tc/build.sh" "$STRATEGY"
      bash "$ROOT_DIR/ebpf/tc/attach.sh" attach "$STRATEGY"
      ;;
    adaptive)
      clear_tc_baseline
      bash "$ROOT_DIR/ebpf/tc/build.sh" adaptive
      bash "$ROOT_DIR/ebpf/tc/attach.sh" attach adaptive

      python3 "$ROOT_DIR/core/bpf_map_ctl.py" set-global \
        --drop-rate 300 \
        --identity-mode "$IDENTITY_MODE" \
        --noisy-ip "10.0.0.4"

      if [[ "$IDENTITY_MODE" == "cgroup" ]]; then
        local noisy_cgid
        noisy_cgid="$(awk -F, '$1=="noisy" {print $3}' "$ROOT_DIR/containers/runtime/cgroup_ids.csv" | tail -n1)"
        if [[ -n "$noisy_cgid" && "$noisy_cgid" != "0" ]]; then
          python3 "$ROOT_DIR/core/bpf_map_ctl.py" set-cgroup --cgroup-id "$noisy_cgid" --drop-rate 300 || true
        fi
      fi

      start_adaptive_controller
      ;;
    *)
      echo "[run-single] Unknown isolation method: $ISOLATION_METHOD"
      exit 1
      ;;
  esac
}

run_failure_mode_background() {
  case "$FAILURE_MODE" in
    none)
      return 0
      ;;
    spike)
      (
        sleep 5
        echo "[run-single] Triggering transient spike"
        tc qdisc del dev veth-noisy root 2>/dev/null || true
        tc qdisc add dev veth-noisy root tbf rate 5mbit burst 16kbit latency 400ms
        sleep 8
        tc qdisc del dev veth-noisy root 2>/dev/null || true
      ) &
      ;;
    churn)
      (
        sleep 7
        echo "[run-single] Triggering noisy container churn"
        runc --root "$RUNC_ROOT" kill noisy KILL || true
        runc --root "$RUNC_ROOT" delete noisy || true
        runc --root "$RUNC_ROOT" run -d --bundle "$ROOT_DIR/containers/runtime/noisy" noisy
        bash "$ROOT_DIR/networking/setup-network.sh" || true
      ) &
      ;;
    extreme)
      (
        sleep 5
        echo "[run-single] Triggering extreme policy"
        if [[ "$ISOLATION_METHOD" == "adaptive" ]]; then
          python3 "$ROOT_DIR/core/bpf_map_ctl.py" set-global --drop-rate 900 --identity-mode "$IDENTITY_MODE" --noisy-ip 10.0.0.4 || true
        else
          tc qdisc del dev veth-noisy root 2>/dev/null || true
          tc qdisc add dev veth-noisy root tbf rate 2mbit burst 8kbit latency 500ms
        fi
      ) &
      ;;
    *)
      echo "[run-single] Unknown failure mode '$FAILURE_MODE' (ignored)"
      ;;
  esac
}

trap cleanup EXIT

echo "[run-single] Starting experiment: $EXP_NAME"
echo "[run-single] noise=$NOISE_LEVEL ebpf=$EBPF_ENABLED containers=$CONTAINERS strategy=$STRATEGY requests=$REQUESTS"
echo "[run-single] isolation=$ISOLATION_METHOD pattern=$TRAFFIC_PATTERN identity=$IDENTITY_MODE iteration=$ITERATION failure=$FAILURE_MODE p99_threshold_ms=$P99_THRESHOLD_MS"
echo "[run-single] resources tenant_cpu=${TENANT_CPU_QUOTA_PCT}% tenant_mem=${TENANT_MEMORY_MB}MB tenant_cpuset=${TENANT_CPUSET} noisy_cpu=${NOISY_CPU_QUOTA_PCT}% noisy_mem=${NOISY_MEMORY_MB}MB noisy_cpuset=${NOISY_CPUSET} host_reserved_cpus=${HOST_RESERVED_CPUS:-none} host_mem_pressure_mb=${HOST_MEM_PRESSURE_MB} io_read_bps=${IO_READ_BPS} io_write_bps=${IO_WRITE_BPS}"

cleanup

CONTAINER_COUNT="$CONTAINERS" NOISE_LEVEL="$NOISE_LEVEL" TRAFFIC_PATTERN="$TRAFFIC_PATTERN" FAILURE_MODE="$FAILURE_MODE" bash "$ROOT_DIR/scripts/start-containers.sh"

write_cgroup_inventory

apply_resource_allocations

apply_isolation

run_failure_mode_background

run_clients

touch "$ADAPTIVE_STOP" 2>/dev/null || true
if [[ -n "$ADAPTIVE_PID" ]]; then
  wait "$ADAPTIVE_PID" 2>/dev/null || true
fi

bash "$ROOT_DIR/scripts/collect-metrics.sh" \
  "$EXP_NAME" "$NOISE_LEVEL" "$EBPF_ENABLED" "$STRATEGY" "$CONTAINERS" \
  "$ISOLATION_METHOD" "$TRAFFIC_PATTERN" "$IDENTITY_MODE" "$ITERATION" "$FAILURE_MODE" "$P99_THRESHOLD_MS" \
  "$TENANT_CPU_QUOTA_PCT" "$TENANT_MEMORY_MB" "$TENANT_CPUSET" \
  "$NOISY_CPU_QUOTA_PCT" "$NOISY_MEMORY_MB" "$NOISY_CPUSET" \
  "$HOST_RESERVED_CPUS" "$HOST_MEM_PRESSURE_MB" "$IO_READ_BPS" "$IO_WRITE_BPS"

echo "[run-single] Experiment complete: $EXP_NAME"
