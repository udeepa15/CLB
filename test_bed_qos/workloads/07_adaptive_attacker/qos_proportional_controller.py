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

VICTIM_IPS = ["10.0.0.10", "10.0.0.11", "10.0.0.12"]
ATTACKER_IP = "10.0.0.20"

PERCPU_MAP_PIN = "/sys/fs/bpf/tc/globals/percpu_rate_limit_map"
COUNTER_MAP_PIN = "/sys/fs/bpf/tc/globals/update_counter_map"

def run_bpftool(cmd_str):
    prefix = "sudo /usr/sbin/bpftool" if os.geteuid() != 0 else "/usr/sbin/bpftool"
    return subprocess.run(f"{prefix} {cmd_str}", shell=True, capture_output=True, text=True)

def ip_to_bpftool_key(ip_str):
    octets = [int(x) for x in ip_str.split('.')]
    return f"{octets[0]} {octets[1]} {octets[2]} {octets[3]}"

def read_update_counter():
    if not os.path.exists(COUNTER_MAP_PIN):
        return 0
    res = run_bpftool(f"map dump pinned {COUNTER_MAP_PIN} -j")
    if res.returncode != 0 or not res.stdout.strip():
        return 0
    try:
        data = json.loads(res.stdout)
        total = 0
        for entry in data:
            values = entry.get("formatted", {}).get("values", []) or entry.get("values", [])
            for v in values:
                try: total += int(v.get("value", 0))
                except Exception: pass
        return total
    except Exception:
        return 0

def update_tenant_rate_limit(ip_str, rate_bytes_per_sec, max_burst_bytes=10000000):
    if not os.path.exists(PERCPU_MAP_PIN):
        return False
    key_str = ip_to_bpftool_key(ip_str)
    num_cpus = os.cpu_count() or 8

    def u64_to_hex_bytes(val):
        b = val.to_bytes(8, byteorder='little')
        return " ".join([f"{x}" for x in b])

    rate_bytes = u64_to_hex_bytes(rate_bytes_per_sec)
    burst_bytes = u64_to_hex_bytes(max_burst_bytes)
    zero_bytes = "0 0 0 0 0 0 0 0"
    single_cpu_val = f"{rate_bytes} {burst_bytes} {burst_bytes} {zero_bytes} {zero_bytes} {zero_bytes}"
    all_cpus_val = " ".join([single_cpu_val for _ in range(num_cpus)])

    res = run_bpftool(f"map update pinned {PERCPU_MAP_PIN} key {key_str} value {all_cpus_val}")
    return res.returncode == 0

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
    update_tenant_rate_limit(ATTACKER_IP, rate_bytes_per_sec=50000000)

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
            update_tenant_rate_limit(ATTACKER_IP, attacker_rate)

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
