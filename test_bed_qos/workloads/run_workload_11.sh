#!/usr/bin/env bash
# run_workload_11.sh - industry benchmarks (workload 11), phases 1-3, config B, 100 tenants.
#   Phase 1  istio      Istio data-plane profile (fortio 1 KB, 1000 RPS, 16 / 4 connections, synchronized and uniform), 8 arms x 10 reps
#   Phase 2  netperf    Cilium CNI tests (TCP_RR / TCP_CRR / TCP_STREAM, 1 and 8 streams), 6 arms x 10 reps
#   Phase 3  memcached  memtier fixed-QPS sweep 10k-80k + saturation, 4 arms x 10 reps
# 10 reps everywhere: with 5 the smallest possible Mann-Whitney p (0.008) cannot pass the Bonferroni threshold.
# Needs /usr/local/bin/envoy (phases 1-2) and memtier_benchmark (phase 3). Run detached:
#   sudo setsid nohup bash run_workload_11.sh > w11.log 2>&1 < /dev/null &
set -u
PHASES="${PHASES:-istio netperf memcached}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_USER="${SUDO_USER:-udeepa}"
export TENANTS_FILE="${TENANTS_FILE:-tenants/scale100.json}" PYTHONDONTWRITEBYTECODE=1
export QOS_CALIBRATION="${QOS_CALIBRATION:-$(dirname "$DIR")/calibration/scale100_rps.json}"
export MEMTIER_BIN="${MEMTIER_BIN:-$(command -v memtier_benchmark || echo /usr/local/bin/memtier_benchmark)}"
B="--tenant-cpus 1-5 --attacker-cpus 6-7 --rps"
[ "$EUID" -eq 0 ] || { echo "run as root"; exit 1; }
# ARMS="a b c" overrides the default arm list of every phase run by this invocation (e.g. to run the
# arms that do not need Envoy first, and the Envoy arms later together with `bare` as the anchor).
ARCH_ARGS=""
if [ -n "${ARMS:-}" ]; then ARCH_ARGS="--architectures $ARMS"; fi
case "${ARMS:-envoy}" in *envoy*)
    [ -x /usr/local/bin/envoy ] || [ -n "${ENVOY_BIN:-}" ] || { echo "Envoy missing (/usr/local/bin/envoy)"; exit 1; } ;;
esac
cd "$DIR/11_benchmarks"
TS=$(date +%Y%m%d_%H%M%S)
for P in $PHASES; do
    case "$P" in
        istio)     REPS=10 ;;
        netperf)   REPS=10 ;;
        memcached) REPS=10; [ -x "$MEMTIER_BIN" ] || { echo "memtier_benchmark missing: skipping memcached"; continue; } ;;
    esac
    OUT="results/${P}${TAG:+_$TAG}_B_N${REPS}_$TS"
    echo "== phase $P $(date) -> $OUT"
    # shellcheck disable=SC2086
    python3 run_bench.py --app "$P" --reps "$REPS" $B $ARCH_ARGS --out "$OUT"
    chown -R "$TARGET_USER" results
    sudo -u "$TARGET_USER" env PYTHONDONTWRITEBYTECODE=1 python3 analyze_bench.py "$OUT" > "$OUT/analysis.txt" 2>&1
done
echo "== workload 11 done $(date)"
