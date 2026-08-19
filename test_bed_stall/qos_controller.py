#!/usr/bin/env python3
"""
qos_controller.py — Userspace Stackelberg Dynamic Rate Controller.

Periodically reads eBPF contention signals (`update_counter_map` hits/sec and `lock_latency_hist`)
directly from pinned BPF maps, computes dynamic Stackelberg leader-follower rate limits,
and writes updated limits to `/sys/fs/bpf/tc/globals/percpu_rate_limit_map` via bpftool.
Logs control loop actions to qos_controller_log.jsonl.

Usage:
    python3 qos_controller.py --log-file results/.../qos_controller_log.jsonl --interval 0.2
"""

import os
import sys
import time
import json
import argparse
import subprocess
from datetime import datetime

# Tenant IPs
VICTIM_IPS = ["10.0.0.10", "10.0.0.11", "10.0.0.12"]  # Leader Tenants (Uncapped)
ATTACKER_IP = "10.0.0.20"                            # Follower Tenant (Rate Limiting Target)

PERCPU_MAP_PIN = "/sys/fs/bpf/tc/globals/percpu_rate_limit_map"
COUNTER_MAP_PIN = "/sys/fs/bpf/tc/globals/update_counter_map"
LATENCY_MAP_PIN = "/sys/fs/bpf/tc/globals/lock_latency_hist"


def run_bpftool(cmd_str):
    prefix = "sudo /usr/sbin/bpftool" if os.geteuid() != 0 else "/usr/sbin/bpftool"
    res = subprocess.run(f"{prefix} {cmd_str}", shell=True, capture_output=True, text=True)
    return res


def ip_to_bpftool_key(ip_str):
    """Convert IPv4 string to bpftool key bytes (little-endian uint32)."""
    octets = [int(x) for x in ip_str.split('.')]
    return f"{octets[0]} {octets[1]} {octets[2]} {octets[3]}"


def read_update_counter():
    """Read total cumulative BPF map update hits from update_counter_map."""
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
                try:
                    total += int(v.get("value", 0))
                except Exception:
                    pass
        return total
    except Exception:
        return 0


def update_tenant_rate_limit(ip_str, rate_bytes_per_sec, max_burst_bytes=10000000):
    """Write dynamic rate limit into percpu_rate_limit_map via bpftool."""
    if not os.path.exists(PERCPU_MAP_PIN):
        return False

    key_str = ip_to_bpftool_key(ip_str)

    # Convert values to 64-bit hex bytes for struct rate_limit_entry:
    # __u64 rate_bytes_per_sec, __u64 max_burst_bytes, __u64 tokens, __u64 last_update_ns, __u64 packets_passed, __u64 packets_dropped
    # Number of CPUs on host (e.g. 8)
    num_cpus = os.cpu_count() or 8

    # Build per-CPU hex value string
    # rate_bytes_per_sec (8 bytes), max_burst_bytes (8 bytes), tokens (8 bytes), last_update (8 bytes), passed (8 bytes), dropped (8 bytes) = 48 bytes
    def u64_to_hex_bytes(val):
        b = val.to_bytes(8, byteorder='little')
        return " ".join([f"{x}" for x in b])

    rate_bytes = u64_to_hex_bytes(rate_bytes_per_sec)
    burst_bytes = u64_to_hex_bytes(max_burst_bytes)
    zero_bytes = "0 0 0 0 0 0 0 0"

    single_cpu_val = f"{rate_bytes} {burst_bytes} {burst_bytes} {zero_bytes} {zero_bytes} {zero_bytes}"
    all_cpus_val = " ".join([single_cpu_val for _ in range(num_cpus)])

    cmd = f"map update pinned {PERCPU_MAP_PIN} key {key_str} value {all_cpus_val}"
    res = run_bpftool(cmd)
    return res.returncode == 0


def solve_stackelberg_rates(hits_per_sec, lock_wait_ns, tenant_ip):
    """
    Swappable Stackelberg Solver Function.
    Leader: Victim traffic (Uncapped: rate_bytes_per_sec = 0)
    Follower: Attacker traffic (Throttled proportionally to contention signal)
    """
    if tenant_ip in VICTIM_IPS:
        return 0  # 0 indicates uncapped leader priority

    # Follower calculation (Attacker: 10.0.0.20)
    # Target baseline rate: 50 MB/s (50,000,000 B/s)
    baseline_rate = 50000000
    min_rate = 500000  # Floor rate: 500 KB/s

    if hits_per_sec > 10000:
        # Scale rate down inversely to eBPF map update hits per second
        scale_factor = max(0.01, 10000.0 / float(hits_per_sec))
        new_rate = int(baseline_rate * scale_factor)
        return max(min_rate, new_rate)
    else:
        return baseline_rate


def main():
    parser = argparse.ArgumentParser(description="Userspace Stackelberg Dynamic Rate Controller")
    parser.add_argument("--log-file", type=str, default="qos_controller_log.jsonl", help="Path to controller log file")
    parser.add_argument("--interval", type=float, default=0.2, help="Control loop interval in seconds")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.log_file)), exist_ok=True)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting Stackelberg QoS Controller (interval={args.interval}s, log={args.log_file})...")

    # Initialize tenant limits
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
            if dt <= 0:
                continue

            current_hits = read_update_counter()
            delta_hits = current_hits - last_hits
            hits_per_sec = float(delta_hits) / dt

            last_hits = current_hits
            last_time = now

            # Compute Stackelberg rates
            attacker_rate = solve_stackelberg_rates(hits_per_sec, 0, ATTACKER_IP)
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
