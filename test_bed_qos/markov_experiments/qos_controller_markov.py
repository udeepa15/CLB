#!/usr/bin/env python3
"""
qos_controller_markov.py — Discrete-Time Markov Chain (DTMC) Predictive Preemptive Controller.

Integrates a Discrete-Time Markov Chain into the user-space controller to predict the next contention
state (LOW, MODERATE, HIGH, SEVERE) based on real-time eBPF map update hits/sec. Pre-emptively calculates
and applies rate limit reductions when the predicted probability of transitioning to HIGH or SEVERE exceeds
the hand-tuned hyperparameter threshold (default 60%).

Note on Model Design:
- Transition count matrix resets to zero at the start of each trial run (t=0) and learns online.
- Optional forgetting (--forget-lambda exponential decay, or --window sliding window) lets the model
  track regime changes; defaults (lambda=1, no window) reproduce the original never-forget behaviour.
- Predict threshold (0.60) is a hand-selected hyperparameter constant.
"""

import os
import sys
import time
import json
import argparse
import math
import subprocess
from collections import deque
from datetime import datetime

# Tenant IPs
VICTIM_IPS = ["10.0.0.10", "10.0.0.11", "10.0.0.12"]  # Leader Tenants (Uncapped)
ATTACKER_IP = "10.0.0.20"                            # Follower Tenant (Rate Limiting Target)

PERCPU_MAP_PIN = "/sys/fs/bpf/tc/globals/percpu_rate_limit_map"
COUNTER_MAP_PIN = "/sys/fs/bpf/tc/globals/update_counter_map"
LATENCY_MAP_PIN = "/sys/fs/bpf/tc/globals/lock_latency_hist"

# DTMC State Definitions
STATE_LOW = 0       # Hits/sec < 5,000
STATE_MODERATE = 1  # 5,000 <= Hits/sec < 15,000
STATE_HIGH = 2      # 15,000 <= Hits/sec < 30,000
STATE_SEVERE = 3    # Hits/sec >= 30,000
STATE_NAMES = ["LOW", "MODERATE", "HIGH", "SEVERE"]


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


def _possible_cpus():
    """Number of possible CPUs (what per-CPU map values are sized by), e.g. '0-7' -> 8."""
    try:
        total = 0
        for part in open("/sys/devices/system/cpu/possible").read().strip().split(","):
            lo, _, hi = part.partition("-")
            total += (int(hi) if hi else int(lo)) - int(lo) + 1
        return total
    except Exception:
        return os.cpu_count() or 8


_WARNED = set()


def update_tenant_rate_limit(ip_str, rate_bytes_per_sec, max_burst_bytes=10000000):
    """Write a rate limit for one tenant into percpu_rate_limit_map via bpftool.

    struct rate_limit_entry = 6 x u64 = 48 bytes: rate, burst, tokens, last_update_ns, passed, dropped.
    bpftool (v5.15.200 verified) takes ONE entry for a per-CPU map and replicates it to every CPU;
    sending one entry per CPU is rejected ("expected key or value"), which made the old version of this
    function fail silently. We try the single-entry form first and fall back to one entry per possible
    CPU for bpftool builds that require it. Failures are reported on stderr (once per distinct error).
    """
    if not os.path.exists(PERCPU_MAP_PIN):
        return False

    key_str = ip_to_bpftool_key(ip_str)

    def u64(val):
        return " ".join(str(x) for x in int(val).to_bytes(8, byteorder="little"))

    zero = "0 0 0 0 0 0 0 0"
    entry = f"{u64(rate_bytes_per_sec)} {u64(max_burst_bytes)} {u64(max_burst_bytes)} {zero} {zero} {zero}"

    res = run_bpftool(f"map update pinned {PERCPU_MAP_PIN} key {key_str} value {entry}")
    if res.returncode != 0:
        res = run_bpftool(f"map update pinned {PERCPU_MAP_PIN} key {key_str} value " +
                          " ".join([entry] * _possible_cpus()))
    if res.returncode != 0:
        err = res.stderr.strip()[:200]
        if err not in _WARNED:
            _WARNED.add(err)
            print(f"[WARN] update_tenant_rate_limit({ip_str}, {rate_bytes_per_sec}) FAILED: {err}",
                  file=sys.stderr, flush=True)
    return res.returncode == 0


def compute_preemptive_rate_limit(hits_per_sec, tenant_ip):
    """
    Sigmoid-based rate limiting solver mapping contention hits/sec to target tenant bandwidth cap.
    """
    if tenant_ip in VICTIM_IPS:
        return 0  # Uncapped leader

    baseline_rate = 50000000  # 50 MB/s (r_max)
    min_rate = 500000         # 500 KB/s (r_min)

    h_threshold = 15000.0     # Contention knee point (hits/sec)
    gamma = 2.0               # Sigmoid sensitivity exponent

    if hits_per_sec <= 0:
        return baseline_rate

    denom = 1.0 + (float(hits_per_sec) / h_threshold) ** gamma
    optimal_rate = int(baseline_rate / denom)

    return max(min_rate, min(baseline_rate, optimal_rate))


def discretize_state(hits_per_sec):
    """Map raw packet update hits/sec to DTMC discrete state."""
    if hits_per_sec < 5000.0:
        return STATE_LOW
    elif hits_per_sec < 15000.0:
        return STATE_MODERATE
    elif hits_per_sec < 30000.0:
        return STATE_HIGH
    else:
        return STATE_SEVERE


class DTMCPredictor:
    """Discrete-Time Markov Chain predictor with Laplace smoothing and optional forgetting.

    forget_lambda: exponential forgetting; every observed transition first multiplies all
        counts by lambda (0 < lambda <= 1). lambda=1.0 is the original never-forget model.
        Effective memory is about 1/(1-lambda) transitions (0.95 -> ~20 ticks).
    window: if set, only the last `window` transitions are kept (sliding window); counts are
        integers and old transitions are subtracted as they leave. Mutually exclusive with
        forget_lambda < 1.
    """
    def __init__(self, alpha=0.1, forget_lambda=1.0, window=None):
        if not 0.0 < forget_lambda <= 1.0:
            raise ValueError("forget_lambda must be in (0, 1]")
        if window is not None and (window < 1 or forget_lambda != 1.0):
            raise ValueError("window must be >= 1 and cannot be combined with forget_lambda < 1")
        self.alpha = alpha
        self.forget_lambda = forget_lambda
        self.window = window
        self.counts = [[0.0 for _ in range(4)] for _ in range(4)]
        self.history = deque()  # (from, to) pairs, only used for the sliding window
        self.last_state = None

    def update(self, current_state):
        if self.last_state is not None:
            if self.forget_lambda < 1.0:
                for row in self.counts:
                    for j in range(4):
                        row[j] *= self.forget_lambda
            self.counts[self.last_state][current_state] += 1.0
            if self.window is not None:
                self.history.append((self.last_state, current_state))
                if len(self.history) > self.window:
                    i, j = self.history.popleft()
                    self.counts[i][j] -= 1.0
        self.last_state = current_state

    def get_transition_matrix(self):
        matrix = [[0.0 for _ in range(4)] for _ in range(4)]
        for i in range(4):
            row_sum = sum(self.counts[i]) + 4 * self.alpha
            for j in range(4):
                matrix[i][j] = (self.counts[i][j] + self.alpha) / row_sum
        return matrix

    def effective_samples(self):
        """Total (decayed) number of transitions currently supporting the model."""
        return sum(sum(r) for r in self.counts)

    def row_entropy(self, state):
        """Shannon entropy (bits) of the predicted next-state distribution for `state`."""
        return -sum(p * math.log2(p) for p in self.get_transition_matrix()[state] if p > 0)

    def predict_high_severe_prob(self, current_state):
        matrix = self.get_transition_matrix()
        # Sum probability of transitioning to HIGH (2) or SEVERE (3)
        prob = matrix[current_state][STATE_HIGH] + matrix[current_state][STATE_SEVERE]
        return prob


def main():
    parser = argparse.ArgumentParser(description="Discrete-Time Markov Chain (DTMC) Predictive Stackelberg Controller")
    parser.add_argument("--log-file", type=str, default="qos_controller_markov_log.jsonl", help="Path to controller log file")
    parser.add_argument("--interval", type=float, default=0.1, help="Control loop polling interval in seconds")
    parser.add_argument("--predict-threshold", type=float, default=0.60, help="Probability threshold to trigger pre-emptive rate cap")
    parser.add_argument("--forget-lambda", type=float, default=1.0,
                        help="Exponential forgetting factor in (0,1]; 1.0 = never forget (original behaviour)")
    parser.add_argument("--window", type=int, default=None,
                        help="Sliding window length in transitions (alternative to --forget-lambda)")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.log_file)), exist_ok=True)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting DTMC Predictive Stackelberg QoS Controller (interval={args.interval}s, predict_threshold={args.predict_threshold}, forget_lambda={args.forget_lambda}, window={args.window}, log={args.log_file})...")

    # Initialize tenant limits
    for v_ip in VICTIM_IPS:
        update_tenant_rate_limit(v_ip, rate_bytes_per_sec=0)
    update_tenant_rate_limit(ATTACKER_IP, rate_bytes_per_sec=50000000)

    dtmc = DTMCPredictor(forget_lambda=args.forget_lambda, window=args.window)
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

            # Discretize current state & update DTMC model
            curr_state = discretize_state(hits_per_sec)
            dtmc.update(curr_state)

            # Calculate transition probability to HIGH/SEVERE
            predict_prob = dtmc.predict_high_severe_prob(curr_state)
            is_preemptive = predict_prob >= args.predict_threshold

            if is_preemptive:
                # Pre-emptive action: assume severe contention (e.g. 35,000 hits/sec) to calculate target rate
                effective_hits = max(hits_per_sec, 35000.0)
                attacker_rate = compute_preemptive_rate_limit(effective_hits, ATTACKER_IP)
            else:
                # Reactive action: standard rate for current hits
                attacker_rate = compute_preemptive_rate_limit(hits_per_sec, ATTACKER_IP)

            update_tenant_rate_limit(ATTACKER_IP, attacker_rate)

            trans_matrix = dtmc.get_transition_matrix()

            record = {
                "timestamp": now,
                "dt_sec": round(dt, 4),
                "hits_per_sec": round(hits_per_sec, 2),
                "delta_hits": delta_hits,
                "current_state": STATE_NAMES[curr_state],
                "current_state_id": curr_state,
                "predicted_high_severe_prob": round(predict_prob, 4),
                "is_preemptive_applied": is_preemptive,
                "attacker_rate_limit_bps": attacker_rate,
                "victim_rate_limit_bps": 0,
                "effective_samples": round(dtmc.effective_samples(), 3),
                "row_entropy_bits": round(dtmc.row_entropy(curr_state), 4),
                "forget_lambda": args.forget_lambda,
                "window": args.window,
                "transition_matrix": [[round(x, 4) for x in row] for row in trans_matrix]
            }

            log_f.write(json.dumps(record) + "\n")
            log_f.flush()


if __name__ == "__main__":
    main()
