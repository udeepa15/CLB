#!/usr/bin/env sh
set -eu

echo "[noisy] Starting CPU + network pressure workload"
TARGETS="${NOISY_TARGETS:-10.0.0.2:8080,10.0.0.3:8080}"
NOISE_LEVEL="${NOISE_LEVEL:-medium}"

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
  *)
    echo "[noisy] Unknown NOISE_LEVEL=$NOISE_LEVEL, using medium"
    CPU_WORKERS="${CPU_WORKERS:-2}"
    NET_PARALLEL="${NET_PARALLEL:-2}"
    NET_SLEEP="${NET_SLEEP:-0.02}"
    ;;
esac

echo "[noisy] level=$NOISE_LEVEL cpu_workers=$CPU_WORKERS net_parallel=$NET_PARALLEL net_sleep=$NET_SLEEP"

# CPU pressure
i=0
while [ "$i" -lt "$CPU_WORKERS" ]; do
  yes > /dev/null &
  i=$((i + 1))
done

# Optional iperf3 server for external traffic generation
iperf3 -s -D >/dev/null 2>&1 || true

net_loop() {
  while true; do
    OLDIFS="$IFS"
    IFS=','
    for target in $TARGETS; do
      curl -s -o /dev/null "http://$target/" || true
    done
    IFS="$OLDIFS"
    sleep "$NET_SLEEP"
  done
}

j=0
while [ "$j" -lt "$NET_PARALLEL" ]; do
  net_loop &
  j=$((j + 1))
done

wait
