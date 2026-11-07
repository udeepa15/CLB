#!/usr/bin/env python3
"""
qos_controller.py — Userspace Stackelberg Dynamic Rate Controller.

Periodically reads eBPF contention signals (`update_counter_map` hits/sec and `lock_latency_hist`)
directly from pinned BPF maps, computes dynamic Stackelberg leader-follower rate limits,
and writes updated limits to `/sys/fs/bpf/tc/globals/rate_cfg_map` (bpf syscall via bpfmap.py).
Logs control loop actions to qos_controller_log.jsonl.

Usage:
    python3 qos_controller.py --log-file results/.../qos_controller_log.jsonl --interval 0.2
"""

import os
import sys
import time
import json
import argparse
import struct
import subprocess
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpfmap  # noqa: E402

# Tenant roles come from the tenants file (tenants.py; $TENANTS_FILE, default tenants/default.json).
# This is ORACLE knowledge: these controllers are told who the attackers are. The automatic arm
# (qos_controller_auto.py) does not use it.
import tenants as _tenants  # noqa: E402
_TOPO = _tenants.load()
VICTIM_IPS = _TOPO.ips("victim")       # Leader tenants (uncapped)
ATTACKER_IPS = _TOPO.ips("attacker")   # Follower tenants (rate-limit targets)
ATTACKER_IP = ATTACKER_IPS[0] if ATTACKER_IPS else None   # backwards-compatible single-attacker name

# Hill knee H0 (hits/s): calibrated per tenants file (calibrate.py), else the original 15000.
import calibration as _calibration  # noqa: E402
CALIBRATION = _calibration.load()
H0 = float(CALIBRATION["h0_hits_per_sec"])

PIN = "/sys/fs/bpf/tc/globals/"
CFG_MAP_PIN = PIN + "rate_cfg_map"          # controller-owned config (rate, burst) - see qos_limiter.h
CNT_MAP_PIN = PIN + "rate_cnt_map"          # kernel-owned per-CPU passed/dropped counters
COUNTER_MAP_PIN = PIN + "update_counter_map"
LATENCY_MAP_PIN = PIN + "lock_latency_hist"


def run_bpftool(cmd_str):
    """Kept for diagnostics/fallbacks only; the control loop uses bpfmap (no fork per call)."""
    prefix = "sudo /usr/sbin/bpftool" if os.geteuid() != 0 else "/usr/sbin/bpftool"
    return subprocess.run(f"{prefix} {cmd_str}", shell=True, capture_output=True, text=True)


def default_burst(rate_bytes_per_sec):
    """Bucket depth used by EVERY arm (static seeds and controllers): 100 ms of the cap, at least
    128 KiB so one 64 KiB GSO super-packet always fits. Arms therefore differ only in the rate."""
    rate = int(rate_bytes_per_sec)
    return max(rate // 10, 131072) if rate > 0 else 0


_possible_cpus = bpfmap.possible_cpus      # backwards-compatible name
_WARNED = set()


def _warn_once(msg):
    if msg not in _WARNED:
        _WARNED.add(msg)
        print(f"[WARN] {msg}", file=sys.stderr, flush=True)


def read_update_counter():
    """Cumulative packets seen by the QoS program at all hooks (sum over CPUs); 0 if not attached."""
    try:
        v = bpfmap.open_pinned(COUNTER_MAP_PIN).lookup(bpfmap.u32_key(0))
        return bpfmap.sum_u64(v) if v else 0
    except OSError as e:
        _warn_once(f"read_update_counter: {e}")
        return 0


def update_tenant_rate_limit(ip_str, rate_bytes_per_sec, max_burst_bytes=None):
    """Set one tenant's cap (rate 0 = uncapped) in rate_cfg_map. Returns True on success.

    Writes CONFIGURATION ONLY (struct rate_cfg: rate, burst). The token-bucket state is owned by
    the kernel, so calling this every tick no longer refills the bucket. (The old version rewrote
    the whole per-CPU entry with tokens = 10 MB each tick, so the cap leaked burst/interval.)
    Failures are warned once per distinct error on stderr instead of being silent."""
    rate = int(rate_bytes_per_sec)
    burst = default_burst(rate) if max_burst_bytes is None else int(max_burst_bytes)
    try:
        bpfmap.open_pinned(CFG_MAP_PIN).update(bpfmap.ip_key(ip_str), struct.pack("=QQ", rate, burst))
        return True
    except (OSError, ValueError) as e:
        _warn_once(f"update_tenant_rate_limit({ip_str}, {rate}) FAILED: {e}")
        return False


def read_tenant_counters(ip_str):
    """(packets_passed, packets_dropped, bytes_passed, bytes_dropped) summed over CPUs, or None."""
    try:
        v = bpfmap.open_pinned(CNT_MAP_PIN).lookup(bpfmap.ip_key(ip_str))
    except OSError:
        return None
    return None if v is None else tuple(bpfmap.sum_u64(v, off) for off in (0, 8, 16, 24))


def solve_stackelberg_rates(hits_per_sec, lock_wait_ns, tenant_ip):
    """
    Game-Theoretic Stackelberg Leader-Follower Rate Limiting Solver.

    Formulation:
    - Leader (QoS Controller): Chooses follower rate cap r in [r_min, r_max]
      to maximize Leader Utility U_L(r) = B_V(r) - C(r, H), where B_V is expected
      victim throughput and C(r, H) is contention penalty from map hits H.
    - Follower (Attacker): Best-response strategy q(r) = r (consumes max allowed bandwidth r).

    Solving dU_L/dr = 0 yields the Stackelberg equilibrium rate cap:
        r*(H) = r_max / (1 + (H / H_threshold)^gamma)
    """
    if tenant_ip in VICTIM_IPS:
        return 0  # 0 indicates uncapped leader (victim) priority

    baseline_rate = 50000000  # 50 MB/s (r_max)
    min_rate = 500000         # 500 KB/s (r_min)

    # Stackelberg Game Parameters
    h_threshold = H0          # Contention knee point (hits/sec), calibrated (calibration.py)
    gamma = 2.0               # Game equilibrium sensitivity exponent

    if hits_per_sec <= 0:
        return baseline_rate

    # Compute Stackelberg Leader-Follower Equilibrium Rate
    denom = 1.0 + (float(hits_per_sec) / h_threshold) ** gamma
    optimal_rate = int(baseline_rate / denom)

    return max(min_rate, min(baseline_rate, optimal_rate))



def main():
    parser = argparse.ArgumentParser(description="Userspace Stackelberg Dynamic Rate Controller")
    parser.add_argument("--log-file", type=str, default="qos_controller_log.jsonl", help="Path to controller log file")
    parser.add_argument("--interval", type=float, default=0.2, help="Baseline control loop interval in seconds")
    parser.add_argument("--adaptive", action="store_true", default=True, help="Enable derivative-based adaptive polling interval")
    parser.add_argument("--no-adaptive", action="store_false", dest="adaptive", help="Disable adaptive polling (use fixed interval)")
    parser.add_argument("--spike-threshold", type=float, default=5000.0, help="Derivative threshold for 20ms spike polling (hits/sec^2)")
    parser.add_argument("--flat-threshold", type=float, default=500.0, help="Derivative threshold for flat calm polling (hits/sec^2)")
    parser.add_argument("--calm-cycles", type=int, default=5, help="Consecutive calm cycles required before relaxing to 1000ms")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.log_file)), exist_ok=True)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting Stackelberg QoS Controller (baseline={args.interval}s, adaptive={args.adaptive}, log={args.log_file})...")

    # Initialize tenant limits
    for v_ip in VICTIM_IPS:
        update_tenant_rate_limit(v_ip, rate_bytes_per_sec=0)
    for a_ip in ATTACKER_IPS:
        update_tenant_rate_limit(a_ip, rate_bytes_per_sec=50000000)

    last_hits = read_update_counter()
    last_time = time.time()
    last_hits_per_sec = 0.0

    current_interval = args.interval
    calm_counter = 0

    with open(args.log_file, "a") as log_f:
        while True:
            time.sleep(current_interval)
            now = time.time()
            t_work = time.perf_counter()
            dt = now - last_time
            if dt <= 0:
                continue

            current_hits = read_update_counter()
            delta_hits = current_hits - last_hits
            hits_per_sec = float(delta_hits) / dt

            # Compute derivative of lock contention metric (dC/dt)
            delta_c = hits_per_sec - last_hits_per_sec
            derivative = delta_c / dt

            last_hits = current_hits
            last_time = now
            last_hits_per_sec = hits_per_sec

            # Adaptive Polling State Machine
            if args.adaptive:
                if derivative > args.spike_threshold or delta_c > args.spike_threshold:
                    # Spike Condition: instantly drop sleep interval to 20ms (0.02s)
                    current_interval = 0.02
                    calm_counter = 0
                elif abs(derivative) < args.flat_threshold:
                    # Calm Condition: increment counter
                    calm_counter += 1
                    if calm_counter >= args.calm_cycles:
                        # Relax interval to 1000ms (1.0s) after N calm cycles
                        current_interval = 1.0
                else:
                    # Default/Recovery: Return to baseline 200ms (0.2s)
                    calm_counter = 0
                    current_interval = args.interval
            else:
                current_interval = args.interval

            # Compute Stackelberg rates
            attacker_rate = solve_stackelberg_rates(hits_per_sec, 0, "attacker")
            for a_ip in ATTACKER_IPS:          # every attacker gets the same Hill cap
                update_tenant_rate_limit(a_ip, attacker_rate)

            record = {
                "timestamp": now,
                "dt_sec": round(dt, 4),
                "hits_per_sec": round(hits_per_sec, 2),
                "delta_hits": delta_hits,
                "contention_derivative": round(derivative, 2),
                "attacker_rate_limit_bps": attacker_rate,
                "victim_rate_limit_bps": 0,
                "polling_interval_ms": int(round(current_interval * 1000)),
                "tick_ms": round((time.perf_counter() - t_work) * 1000, 3)  # work per tick, excl. sleep
            }

            log_f.write(json.dumps(record) + "\n")
            log_f.flush()


if __name__ == "__main__":
    main()

