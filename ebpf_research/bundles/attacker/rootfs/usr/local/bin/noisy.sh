#!/usr/bin/env sh
set -eu

echo "[noisy] Starting CPU + network pressure workload"
TARGETS="${NOISY_TARGETS:-10.0.0.2:8080,10.0.0.3:8080}"
NOISE_LEVEL="${NOISE_LEVEL:-medium}"
TRAFFIC_PATTERN="${TRAFFIC_PATTERN:-constant}"
FAILURE_MODE="${FAILURE_MODE:-none}"
START_DELAY_S="${NOISE_START_DELAY_S:-5}"
INTERMITTENT_ON_S="${INTERMITTENT_ON_S:-4}"
INTERMITTENT_OFF_S="${INTERMITTENT_OFF_S:-4}"
BURST_S="${BURST_S:-3}"
BASE_S="${BASE_S:-5}"

case "$NOISE_LEVEL" in
  low)
    CPU_WORKERS="${CPU_WORKERS:-1}"
    NET_PARALLEL="${NET_PARALLEL:-1}"
    NET_SLEEP="${NET_SLEEP:-0.08}"
    ;;
  medium)
    CPU_WORKERS="${CPU_WORKERS:-2}"
    NET_PARALLEL="${NET_PARALLEL:-2}"
    NET_SLEEP="${NET_SLEEP:-0.02}"
    ;;
  high)
    CPU_WORKERS="${CPU_WORKERS:-4}"
    NET_PARALLEL="${NET_PARALLEL:-4}"
    NET_SLEEP="${NET_SLEEP:-0.001}"
    ;;
  extreme)
    CPU_WORKERS="${CPU_WORKERS:-6}"
    NET_PARALLEL="${NET_PARALLEL:-8}"
    NET_SLEEP="${NET_SLEEP:-0.0005}"
    ;;
  *)
    echo "[noisy] Unknown NOISE_LEVEL=$NOISE_LEVEL, using medium"
    CPU_WORKERS="${CPU_WORKERS:-2}"
    NET_PARALLEL="${NET_PARALLEL:-2}"
    NET_SLEEP="${NET_SLEEP:-0.02}"
    ;;
esac

echo "[noisy] level=$NOISE_LEVEL pattern=$TRAFFIC_PATTERN failure_mode=$FAILURE_MODE cpu_workers=$CPU_WORKERS net_parallel=$NET_PARALLEL net_sleep=$NET_SLEEP"

# CPU pressure
i=0
while [ "$i" -lt "$CPU_WORKERS" ]; do
  yes > /dev/null &
  i=$((i + 1))
done

# Optional iperf3 server for external traffic generation
iperf3 -s -D >/dev/null 2>&1 || true

net_once() {
  OLDIFS="$IFS"
  IFS=','
  for target in $TARGETS; do
    curl -s -o /dev/null "http://$target/" || true
  done
  IFS="$OLDIFS"
}

net_loop_constant() {
  while true; do
    net_once
    sleep "$NET_SLEEP"
  done
}

net_loop_bursty() {
  while true; do
    end=$(( $(date +%s) + BURST_S ))
    while [ "$(date +%s)" -lt "$end" ]; do
      net_once
      sleep "0.0005"
    done
    sleep "$BASE_S"
  done
}

net_loop_intermittent() {
  while true; do
    end_on=$(( $(date +%s) + INTERMITTENT_ON_S ))
    while [ "$(date +%s)" -lt "$end_on" ]; do
      net_once
      sleep "$NET_SLEEP"
    done
    sleep "$INTERMITTENT_OFF_S"
  done
}

case "$TRAFFIC_PATTERN" in
  delayed_start)
    echo "[noisy] delaying noisy start by ${START_DELAY_S}s"
    sleep "$START_DELAY_S"
    runner="net_loop_constant"
    ;;
  bursty)
    runner="net_loop_bursty"
    ;;
  intermittent)
    runner="net_loop_intermittent"
    ;;
  constant)
    runner="net_loop_constant"
    ;;
  *)
    echo "[noisy] Unknown TRAFFIC_PATTERN=$TRAFFIC_PATTERN, falling back to constant"
    runner="net_loop_constant"
    ;;
esac

if [ "$FAILURE_MODE" = "spike" ]; then
  NET_PARALLEL=$((NET_PARALLEL + 2))
fi

if [ "$FAILURE_MODE" = "extreme" ]; then
  NET_PARALLEL=$((NET_PARALLEL + 4))
fi

j=0
while [ "$j" -lt "$NET_PARALLEL" ]; do
  $runner &
  j=$((j + 1))
done

wait
