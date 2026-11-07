#!/usr/bin/env python3
"""
qos_proportional_controller.py — Linear Proportional Feedback Controller for Workload 07.

Serves as the third arm in the Workload 07 3-way ablation study:
1. qos_tiered (Static priority boundary)
2. qos_proportional (Naive linear proportional feedback: Limit = Baseline - Kp * HitsPerSec)
3. qos_dynamic (Stackelberg game-theoretic Leader-Follower rate solver)
"""

import os
import sys
import time
import json
import argparse
import subprocess
from datetime import datetime


COUNTER_MAP_PIN = "/sys/fs/bpf/tc/globals/update_counter_map"

# Map I/O is shared with qos_controller.py (bpf syscall via bpfmap.py; config-only writes, see
# qos_limiter.h). Private copies used to drift apart; one implementation now serves every controller.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '../..')))
from qos_controller import read_update_counter, update_tenant_rate_limit  # noqa: E402
from qos_controller import VICTIM_IPS, ATTACKER_IPS  # noqa: E402  (roles from the tenants file)


def solve_proportional_rates(hits_per_sec):
    """Linear Proportional Feedback: Limit = Baseline - Kp * HitsPerSec."""
    baseline_rate = 50000000  # 50 MB/s
    kp = 2000.0               # Proportional Gain Constant
    reduction = int(kp * hits_per_sec)
    new_rate = baseline_rate - reduction
    return max(500000, min(baseline_rate, new_rate))

def main():
    parser = argparse.ArgumentParser(description="Linear Proportional QoS Controller")
    parser.add_argument("--log-file", type=str, default="qos_controller_log.jsonl")
    parser.add_argument("--interval", type=float, default=0.2)
    args = parser.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.log_file)), exist_ok=True)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting Linear Proportional Controller...")

    for v_ip in VICTIM_IPS:
        update_tenant_rate_limit(v_ip, rate_bytes_per_sec=0)
    for a_ip in ATTACKER_IPS:
        update_tenant_rate_limit(a_ip, rate_bytes_per_sec=50000000)

    last_hits = read_update_counter()
    last_time = time.time()

    with open(args.log_file, "a") as log_f:
        while True:
            time.sleep(args.interval)
            now = time.time()
            dt = now - last_time
            if dt <= 0: continue

            current_hits = read_update_counter()
            delta_hits = current_hits - last_hits
            hits_per_sec = float(delta_hits) / dt

            last_hits = current_hits
            last_time = now

            attacker_rate = solve_proportional_rates(hits_per_sec)
            for a_ip in ATTACKER_IPS:          # every attacker gets the same cap
                update_tenant_rate_limit(a_ip, attacker_rate)

            record = {
                "timestamp": now,
                "dt_sec": round(dt, 4),
                "hits_per_sec": round(hits_per_sec, 2),
                "delta_hits": delta_hits,
                "attacker_rate_limit_bps": attacker_rate,
                "victim_rate_limit_bps": 0
            }
            log_f.write(json.dumps(record) + "\n")
            log_f.flush()

if __name__ == "__main__":
    main()
