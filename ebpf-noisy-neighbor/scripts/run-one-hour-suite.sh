#!/usr/bin/env bash
set -euo pipefail

# One-hour research smoke suite.
# Goal: cover all major test types (none/tc/ebpf/adaptive, ip/cgroup,
# multiple noise/pattern/failure/resource modes) plus advanced tests
# (XDP vs TC hook comparison + map-pressure churn) within ~60 minutes.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TS="$(date +%Y%m%d_%H%M%S)"
TMP_CFG="$(mktemp /tmp/one_hour_suite_${TS}_XXXX.yaml)"

REQUESTS="${REQUESTS:-200}"
MAP_CHURN_DURATION_S="${MAP_CHURN_DURATION_S:-20}"
TARGET_UPDATES_PER_SEC="${TARGET_UPDATES_PER_SEC:-60000}"
NOISE_FOR_ADVANCED="${NOISE_FOR_ADVANCED:-high}"
INCLUDE_ADVANCED="${INCLUDE_ADVANCED:-true}"

if [[ ${EUID} -ne 0 ]]; then
  echo "[one-hour-suite] Please run as root (sudo)."
  exit 1
fi

cleanup() {
  rm -f "$TMP_CFG" 2>/dev/null || true
}
trap cleanup EXIT

cat > "$TMP_CFG" <<YAML
global:
  iterations: 1
  requests_per_client: ${REQUESTS}
  p99_threshold_ms: 8.0
  sample_interval_s: 1.0

scenarios:
  # --- baseline/no isolation ---
  - name: ohs_none_low_constant_none_${TS}
    noise_level: low
    traffic_pattern: constant
    isolation_method: none
    identity_mode: ip
    container_count: 3
    failure_mode: none
    requests: ${REQUESTS}
    tenant_cpu_quota_pct: 80
    tenant_memory_mb: 512
    noisy_cpu_quota_pct: 60
    noisy_memory_mb: 384

  - name: ohs_none_medium_bursty_spike_${TS}
    noise_level: medium
    traffic_pattern: bursty
    isolation_method: none
    identity_mode: ip
    container_count: 3
    failure_mode: spike
    requests: ${REQUESTS}

  # --- tc baseline ---
  - name: ohs_tc_high_intermittent_churn_${TS}
    noise_level: high
    traffic_pattern: intermittent
    isolation_method: tc
    identity_mode: ip
    container_count: 3
    failure_mode: churn
    requests: ${REQUESTS}

  - name: ohs_tc_medium_delayed_none_${TS}
    noise_level: medium
    traffic_pattern: delayed_start
    isolation_method: tc
    identity_mode: ip
    container_count: 3
    failure_mode: none
    requests: ${REQUESTS}

  # --- static ebpf strategies ---
  - name: ohs_ebpf_dropper_high_bursty_spike_${TS}
    noise_level: high
    traffic_pattern: bursty
    isolation_method: ebpf
    ebpf_strategy: dropper
    identity_mode: ip
    container_count: 3
    failure_mode: spike
    requests: ${REQUESTS}

  - name: ohs_ebpf_rate_medium_intermittent_none_${TS}
    noise_level: medium
    traffic_pattern: intermittent
    isolation_method: ebpf
    ebpf_strategy: rate_limit
    identity_mode: ip
    container_count: 3
    failure_mode: none
    requests: ${REQUESTS}

  - name: ohs_ebpf_priority_low_delayed_churn_${TS}
    noise_level: low
    traffic_pattern: delayed_start
    isolation_method: ebpf
    ebpf_strategy: priority
    identity_mode: ip
    container_count: 3
    failure_mode: churn
    requests: ${REQUESTS}

  # --- adaptive with ip + cgroup ---
  - name: ohs_adaptive_ip_low_constant_none_${TS}
    noise_level: low
    traffic_pattern: constant
    isolation_method: adaptive
    identity_mode: ip
    container_count: 3
    failure_mode: none
    requests: ${REQUESTS}

  - name: ohs_adaptive_cgroup_high_bursty_spike_${TS}
    noise_level: high
    traffic_pattern: bursty
    isolation_method: adaptive
    identity_mode: cgroup
    container_count: 3
    failure_mode: spike
    requests: ${REQUESTS}

  # --- resource-variation focused ---
  - name: ohs_resource_constrained_ebpf_${TS}
    noise_level: medium
    traffic_pattern: constant
    isolation_method: ebpf
    ebpf_strategy: rate_limit
    identity_mode: ip
    container_count: 5
    failure_mode: none
    requests: ${REQUESTS}
    tenant_cpu_quota_pct: 60
    tenant_memory_mb: 384
    noisy_cpu_quota_pct: 40
    noisy_memory_mb: 256
    host_mem_pressure_mb: 256

  - name: ohs_resource_reserved_cpu_adaptive_${TS}
    noise_level: medium
    traffic_pattern: delayed_start
    isolation_method: adaptive
    identity_mode: cgroup
    container_count: 5
    failure_mode: churn
    requests: ${REQUESTS}
    tenant_cpu_quota_pct: 70
    tenant_memory_mb: 448
    noisy_cpu_quota_pct: 50
    noisy_memory_mb: 320
    host_reserved_cpus: "0"

matrix: {}
YAML

echo "[one-hour-suite] Running core coverage scenarios..."
"$ROOT_DIR/scripts/run-all-experiments.sh" "$TMP_CFG" --skip-finalize

if [[ "$INCLUDE_ADVANCED" == "true" ]]; then
  echo "[one-hour-suite] Running XDP vs TC benchmark (rate_limit)..."
  REQUESTS="$REQUESTS" NOISE_LEVEL="$NOISE_FOR_ADVANCED" bash "$ROOT_DIR/experiments/hook-benchmark/run.sh" rate_limit

  echo "[one-hour-suite] Running map-pressure churn test..."
  REQUESTS="$REQUESTS" MAP_CHURN_DURATION_S="$MAP_CHURN_DURATION_S" TARGET_UPDATES_PER_SEC="$TARGET_UPDATES_PER_SEC" \
    bash "$ROOT_DIR/experiments/map-pressure/run.sh" "ohs_map_pressure_${TS}"
fi

echo "[one-hour-suite] Finalizing outputs..."
python3 "$ROOT_DIR/core/finalize_results.py" --root "$ROOT_DIR" --config "$TMP_CFG"

echo "[one-hour-suite] Done."
echo "[one-hour-suite] Temporary config: $TMP_CFG (removed at exit)"
echo "[one-hour-suite] Tip: set INCLUDE_ADVANCED=false for a faster run (< 1h on slower hosts)."
