#!/usr/bin/env sh
set -eu

echo "[noisy] Starting CPU + network pressure workload"
TARGETS="${NOISY_TARGETS:-10.0.0.2:8080,10.0.0.3:8080}"
CPU_WORKERS="${CPU_WORKERS:-2}"

# CPU pressure
i=0
while [ "$i" -lt "$CPU_WORKERS" ]; do
  yes > /dev/null &
  i=$((i + 1))
done

# Optional iperf3 server for external traffic generation
iperf3 -s -D >/dev/null 2>&1 || true

# Network pressure loop against victim tenants
while true; do
  OLDIFS="$IFS"
  IFS=','
  for target in $TARGETS; do
    curl -s -o /dev/null "http://$target/" || true
  done
  IFS="$OLDIFS"
  sleep 0.01
done
