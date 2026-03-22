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

ADAPTIVE_PID=""
ADAPTIVE_STOP="$RAW_DIR/.adaptive_stop"

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
    "$ROOT_DIR/workloads/client.sh" "$url" "$REQUESTS" "$prefix"
  done < "$inventory"
}

cleanup() {
  touch "$ADAPTIVE_STOP" 2>/dev/null || true
  if [[ -n "${ADAPTIVE_PID}" ]]; then
    kill "$ADAPTIVE_PID" 2>/dev/null || true
    wait "$ADAPTIVE_PID" 2>/dev/null || true
  fi
  "$ROOT_DIR/scripts/stop-containers.sh" || true
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
      "$ROOT_DIR/ebpf/tc/attach.sh" detach || true
      ;;
    tc)
      clear_tc_baseline
      "$ROOT_DIR/ebpf/tc/attach.sh" detach || true
      apply_tc_baseline
      ;;
    ebpf)
      clear_tc_baseline
      "$ROOT_DIR/ebpf/tc/build.sh" "$STRATEGY"
      "$ROOT_DIR/ebpf/tc/attach.sh" attach "$STRATEGY"
      ;;
    adaptive)
      clear_tc_baseline
      "$ROOT_DIR/ebpf/tc/build.sh" adaptive
      "$ROOT_DIR/ebpf/tc/attach.sh" attach adaptive

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
        "$ROOT_DIR/networking/setup-network.sh" || true
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

cleanup

CONTAINER_COUNT="$CONTAINERS" NOISE_LEVEL="$NOISE_LEVEL" TRAFFIC_PATTERN="$TRAFFIC_PATTERN" FAILURE_MODE="$FAILURE_MODE" "$ROOT_DIR/scripts/start-containers.sh"

write_cgroup_inventory

apply_isolation

run_failure_mode_background

run_clients

touch "$ADAPTIVE_STOP" 2>/dev/null || true
if [[ -n "$ADAPTIVE_PID" ]]; then
  wait "$ADAPTIVE_PID" 2>/dev/null || true
fi

"$ROOT_DIR/scripts/collect-metrics.sh" \
  "$EXP_NAME" "$NOISE_LEVEL" "$EBPF_ENABLED" "$STRATEGY" "$CONTAINERS" \
  "$ISOLATION_METHOD" "$TRAFFIC_PATTERN" "$IDENTITY_MODE" "$ITERATION" "$FAILURE_MODE" "$P99_THRESHOLD_MS"

echo "[run-single] Experiment complete: $EXP_NAME"
