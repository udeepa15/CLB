#!/usr/bin/env python3
"""
collect_ebpf_stats.py — Poll eBPF maps (lock_latency_hist, limiter_only_latency_hist, update_counter_map).

Poller script for Direction 2: Isolates limiter-only latency histogram (`limiter_only_latency_hist`)
from full-path latency histogram (`lock_latency_hist`).

Usage:
    collect_ebpf_stats.py <output_jsonl>
"""

import sys
import time
import subprocess
import json

def get_bpf_map(map_name):
    path = f"/sys/fs/bpf/tc/globals/{map_name}"
    res = subprocess.run(['sudo', 'bpftool', 'map', 'dump', 'pinned', path, '-j'], capture_output=True, text=True)
    if res.returncode != 0:
        return None
    try:
        return json.loads(res.stdout)
    except:
        return None

def extract_total_counter(counter_json):
    """Sum values across all per-CPU entries in update_counter_map."""
    if not counter_json:
        return 0
    total = 0
    for entry in counter_json:
        values = entry.get("formatted", {}).get("values", []) or entry.get("values", [])
        for v in values:
            try:
                total += int(v.get("value", 0))
            except:
                pass
    return total

def main():
    if len(sys.argv) < 2:
        print('Usage: collect_ebpf_stats.py <output_jsonl>')
        sys.exit(1)
    
    out_file = sys.argv[1]
    
    prev_ts = None
    prev_counter = 0

    with open(out_file, 'w') as f:
        try:
            while True:
                ts = time.time()
                full_hist_data = get_bpf_map("lock_latency_hist")
                limiter_hist_data = get_bpf_map("limiter_only_latency_hist")
                cnt_data = get_bpf_map("update_counter_map")
                
                curr_counter = extract_total_counter(cnt_data)
                hits_per_sec = 0.0
                interval_count = 0

                if prev_ts is not None:
                    dt = ts - prev_ts
                    if dt > 0:
                        interval_count = max(0, curr_counter - prev_counter)
                        hits_per_sec = round(interval_count / dt, 2)

                prev_ts = ts
                prev_counter = curr_counter

                record = {
                    'timestamp': ts,
                    'hits_per_sec': hits_per_sec,
                    'interval_hits': interval_count,
                    'cumulative_hits': curr_counter,
                    'histogram': full_hist_data,
                    'limiter_histogram': limiter_hist_data
                }
                f.write(json.dumps(record) + '\n')
                f.flush()
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass

if __name__ == '__main__':
    main()
