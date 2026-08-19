#!/usr/bin/env python3
"""
collect_runq_stats.py — Python Poller/Wrapper for CFS Runqueue Latency Tracing.

Runs `bpftrace collect_runq_latency.bt` in the background during trials
and outputs timestamped JSONL records to disk containing runqueue latency metrics.

Usage:
    python3 collect_runq_stats.py <output_jsonl>
"""

import os
import sys
import time
import json
import subprocess
from datetime import datetime

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 collect_runq_stats.py <output_jsonl>")
        sys.exit(1)

    out_file = sys.argv[1]
    os.makedirs(os.path.dirname(os.path.abspath(out_file)), exist_ok=True)

    bt_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "collect_runq_latency.bt")
    cmd = ["sudo", "bpftrace", bt_script]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(1.0)  # Allow bpftrace to attach

    start_ts = time.time()
    with open(out_file, "w") as f:
        try:
            while True:
                time.sleep(1.0)
                ts = time.time()
                record = {
                    "timestamp": ts,
                    "elapsed_sec": round(ts - start_ts, 2),
                    "status": "tracing",
                    "pid": proc.pid
                }
                f.write(json.dumps(record) + "\n")
                f.flush()
        except KeyboardInterrupt:
            pass
        finally:
            import signal
            proc.send_signal(signal.SIGINT)
            try:
                stdout, stderr = proc.communicate(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
            end_record = {
                "timestamp": time.time(),
                "status": "complete",
                "bpftrace_output": stdout if stdout else ""
            }
            with open(out_file, "a") as f_end:
                f_end.write(json.dumps(end_record) + "\n")

if __name__ == "__main__":
    main()
