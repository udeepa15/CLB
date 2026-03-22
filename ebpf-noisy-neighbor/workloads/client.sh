#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "Usage: $0 <url> <requests> <output-prefix>"
  exit 1
fi

URL="$1"
REQUESTS="$2"
OUT_PREFIX="$3"
OUT_FILE="${OUT_PREFIX}.latency"
LOG_FILE="${OUT_PREFIX}.log"
TS_FILE="${OUT_PREFIX}.timeseries.csv"

mkdir -p "$(dirname "$OUT_FILE")"

echo "# target=$URL" > "$OUT_FILE"
echo "# requests=$REQUESTS" >> "$OUT_FILE"
echo "index,epoch_s,latency_ms" > "$TS_FILE"

echo "[client] Sending $REQUESTS requests to $URL"
failures=0
start_epoch="$(date +%s.%N)"
for ((i=1; i<=REQUESTS; i++)); do
  ts="$(date +%Y-%m-%dT%H:%M:%S.%N%:z)"
  epoch="$(date +%s.%N)"
  t="$(curl -s -o /dev/null -w '%{time_total}' "$URL" 2>/dev/null || true)"

  if [[ -z "$t" ]]; then
    failures=$((failures + 1))
    continue
  fi

  ms="$(python3 - <<PY
v=float("$t")
print(v*1000.0)
PY
)"
  echo "$ts,$ms" >> "$OUT_FILE"
    echo "$i,$epoch,$ms" >> "$TS_FILE"
done
end_epoch="$(date +%s.%N)"

  python3 - "$OUT_FILE" "$TS_FILE" "$URL" "$REQUESTS" "$failures" "$start_epoch" "$end_epoch" <<'PY' | tee "$LOG_FILE"
import sys
  from statistics import mean, pstdev

  path, ts_file, url, requests, failures, start_epoch, end_epoch = sys.argv[1:8]
vals = []
with open(path, "r", encoding="utf-8") as f:
    for line in f:
        if line.startswith("#"):
            continue
      parts = line.strip().rsplit(",", 1)
        if len(parts) != 2:
            continue
      try:
        vals.append(float(parts[1]))
      except ValueError:
        continue

  ts_vals = []
  with open(ts_file, "r", encoding="utf-8") as f:
    next(f, None)
    for line in f:
      parts = line.strip().split(",")
      if len(parts) != 3:
        continue
      try:
        ts_vals.append(float(parts[2]))
      except ValueError:
        continue

if not vals:
    print(
      f"SUMMARY target={url} requests={requests} failures={failures} "
      "p50_ms=nan p95_ms=nan p99_ms=nan jitter_ms=nan variance_ms2=nan "
      "throughput_rps=nan avg_ms=nan"
    )
    raise SystemExit(0)

vals.sort()

def pct(p):
    idx = int(round((p / 100.0) * (len(vals) - 1)))
    return vals[max(0, min(idx, len(vals)-1))]

p50 = pct(50)
p95 = pct(95)
p99 = pct(99)
avg = mean(vals)
jitter = pstdev(vals) if len(vals) > 1 else 0.0
variance = jitter ** 2

ok = len(vals)
elapsed = max(float(end_epoch) - float(start_epoch), 1e-9)
throughput = ok / elapsed

print(
  f"SUMMARY target={url} requests={requests} failures={failures} "
  f"p50_ms={p50:.3f} p95_ms={p95:.3f} p99_ms={p99:.3f} jitter_ms={jitter:.3f} "
  f"variance_ms2={variance:.3f} throughput_rps={throughput:.3f} avg_ms={avg:.3f}"
)
PY
