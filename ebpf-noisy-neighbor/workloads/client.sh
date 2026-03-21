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

mkdir -p "$(dirname "$OUT_FILE")"

echo "# target=$URL" > "$OUT_FILE"
echo "# requests=$REQUESTS" >> "$OUT_FILE"

echo "[client] Sending $REQUESTS requests to $URL"
failures=0
for ((i=1; i<=REQUESTS; i++)); do
  ts="$(date --iso-8601=ns)"
  t="$(curl -sS -o /dev/null -w '%{time_total}' "$URL" || true)"

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
done

python3 - "$OUT_FILE" "$URL" "$REQUESTS" "$failures" <<'PY' | tee "$LOG_FILE"
import sys
from statistics import mean

path, url, requests, failures = sys.argv[1:5]
vals = []
with open(path, "r", encoding="utf-8") as f:
    for line in f:
        if line.startswith("#"):
            continue
        parts = line.strip().split(",")
        if len(parts) != 2:
            continue
        vals.append(float(parts[1]))

if not vals:
    print(f"SUMMARY target={url} requests={requests} failures={failures} p50_ms=nan p90_ms=nan p99_ms=nan avg_ms=nan")
    raise SystemExit(0)

vals.sort()

def pct(p):
    idx = int(round((p / 100.0) * (len(vals) - 1)))
    return vals[max(0, min(idx, len(vals)-1))]

p50 = pct(50)
p90 = pct(90)
p99 = pct(99)
avg = mean(vals)
print(f"SUMMARY target={url} requests={requests} failures={failures} p50_ms={p50:.3f} p90_ms={p90:.3f} p99_ms={p99:.3f} avg_ms={avg:.3f}")
PY
