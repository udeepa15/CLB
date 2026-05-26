#!/usr/bin/env bash
# aggregate_results.sh: Run this to parse your experiments into the target CSV format.

echo "Architecture,Attacker_RPS,P50_Latency_ms,P90_Latency_ms,P99_Latency_ms,P999_Latency_ms,Context_Switches,Spinlock_Contention"

for arch in sidecar sidecarless; do
    # Find the latest timestamped folder
    LATEST_DIR=$(ls -td results/$arch/* 2>/dev/null | head -n 1)
    if [ -z "$LATEST_DIR" ]; then continue; fi
    
    for rps in 0 10000 20000 30000; do
        JSON_FILE="$LATEST_DIR/fortio_rps_${rps}.json"
        LOG_FILE="$LATEST_DIR/bpftrace_rps_${rps}.log"
        
        if [ ! -f "$JSON_FILE" ]; then continue; fi
        
        # Parse percentiles from fortio json
        # fortio stores values in seconds, so we multiply by 1000 to get ms.
        P50=$(jq '.DurationHistogram.Percentiles[] | select(.Percentile == 50) | .Value * 1000' "$JSON_FILE")
        P90=$(jq '.DurationHistogram.Percentiles[] | select(.Percentile == 90) | .Value * 1000' "$JSON_FILE")
        P99=$(jq '.DurationHistogram.Percentiles[] | select(.Percentile == 99) | .Value * 1000' "$JSON_FILE")
        P999=$(jq '.DurationHistogram.Percentiles[] | select(.Percentile == 99.9) | .Value * 1000' "$JSON_FILE")
        
        # Parse counts from bpftrace logs
        CS_COUNT="N/A"
        LOCK_COUNT="0"
        if [ -f "$LOG_FILE" ]; then
            # Context switches count is stored as `@context_switches: <number>` or within a print output in newer versions
            CS_COUNT=$(grep -oP '@context_switches: \K\d+' "$LOG_FILE" || grep -A1 '@context_switches' "$LOG_FILE" | grep -v '@context_switches' | tr -d '[:space:]' || echo "N/A")
            # Spinlock contention count
            LOCK_COUNT=$(grep -oP '@spinlock_contention_count: \K\d+' "$LOG_FILE" || grep -A1 '@spinlock_contention_count' "$LOG_FILE" | grep -v '@spinlock_contention_count' | tr -d '[:space:]' || echo "0")
        fi
        
        # clean up any empty outputs
        if [ -z "$CS_COUNT" ]; then CS_COUNT="N/A"; fi
        if [ -z "$LOCK_COUNT" ]; then LOCK_COUNT="0"; fi
        
        printf "%s,%d,%.3f,%.3f,%.3f,%.3f,%s,%s\n" \
            "$arch" "$rps" "$P50" "$P90" "$P99" "$P999" "$CS_COUNT" "$LOCK_COUNT"
    done
done
