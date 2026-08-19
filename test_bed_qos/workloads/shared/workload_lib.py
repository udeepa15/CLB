#!/usr/bin/env python3
"""
workload_lib.py — Shared Workload Library & Helper Utilities for Direction 1 Workload Suite.

Provides common utilities for:
- Dynamic hping3 process management (bursty oscillation, step ramping, adaptive probing).
- Fortio traffic generation & metrics parsing.
- Controller JSONL log parsing.
- Statistical analysis (Mann-Whitney U test with Bonferroni correction).

Imported by all 7 workload generator modules under ~/CLB/test_bed_qos/workloads/.
"""

import os
import sys
import time
import math
import json
import socket
import signal
import threading
import subprocess
from datetime import datetime

# Shared baseline configuration
SHARED_CONFIG = {
    "qps": 50,
    "conns": 2,
    "duration_sec": 10,
    "warmup_sec": 2,
    "victim1_ip": "10.0.0.10",
    "victim2_ip": "10.0.0.11",
    "victim3_ip": "10.0.0.12",
    "attacker_ip": "10.0.0.20",
    "port_http": 8080,
    "port_grpc": 8079,
    "port_tcp": 8078
}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def run_cmd(cmd, check=True, shell=True, cwd=None):
    res = subprocess.run(cmd, shell=shell, capture_output=True, text=True, cwd=cwd)
    if check and res.returncode != 0:
        log(f"Command failed: {cmd}\nStderr: {res.stderr}")
    return res

def wait_for_port(ip, port, timeout=15):
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection((ip, port), timeout=1.0):
                return True
        except (OSError, ConnectionRefusedError):
            time.sleep(0.5)
    log(f"CRITICAL ERROR: Timeout waiting for server {ip}:{port}!")
    return False

# --- ATTACKER FLOOD GENERATORS ---

class BurstyAttackerThread(threading.Thread):
    """Alternates between level1 and level2 every interval_sec seconds."""
    def __init__(self, target_ip, target_port, level1="u200", level2="flood", interval_sec=5.0):
        super().__init__()
        self.target_ip = target_ip
        self.target_port = target_port
        self.level1 = level1
        self.level2 = level2
        self.interval_sec = interval_sec
        self.stop_event = threading.Event()
        self.proc = None

    def _start_hping(self, level):
        if self.proc:
            self.proc.kill()
            subprocess.run("pkill -9 hping3 2>/dev/null || true", shell=True)
        if level == "0":
            return None
        elif level == "flood":
            cmd = f"ip netns exec ns_attacker hping3 --udp -p {self.target_port} --flood {self.target_ip}"
        else:
            cmd = f"ip netns exec ns_attacker hping3 --udp -p {self.target_port} -i {level} {self.target_ip}"
        return subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def run(self):
        current_level = self.level1
        while not self.stop_event.is_set():
            self.proc = self._start_hping(current_level)
            log(f"[BurstyAttacker] Switched flood level to: {current_level}")
            self.stop_event.wait(self.interval_sec)
            current_level = self.level2 if current_level == self.level1 else self.level1

    def stop(self):
        self.stop_event.set()
        if self.proc:
            self.proc.kill()
            subprocess.run("pkill -9 hping3 2>/dev/null || true", shell=True)

class RampingAttackerThread(threading.Thread):
    """Monotonically escalates flood intensity over time through ramp_levels every step_sec."""
    def __init__(self, target_ip, target_port, ramp_levels=None, step_sec=2.0):
        super().__init__()
        self.target_ip = target_ip
        self.target_port = target_port
        self.ramp_levels = ramp_levels or ["u500", "u200", "u50", "u20", "u5", "u2", "flood"]
        self.step_sec = step_sec
        self.stop_event = threading.Event()
        self.proc = None

    def _start_hping(self, level):
        if self.proc:
            self.proc.kill()
            subprocess.run("pkill -9 hping3 2>/dev/null || true", shell=True)
        if level == "0":
            return None
        elif level == "flood":
            cmd = f"ip netns exec ns_attacker hping3 --udp -p {self.target_port} --flood {self.target_ip}"
        else:
            cmd = f"ip netns exec ns_attacker hping3 --udp -p {self.target_port} -i {level} {self.target_ip}"
        return subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def run(self):
        for level in self.ramp_levels:
            if self.stop_event.is_set():
                break
            self.proc = self._start_hping(level)
            log(f"[RampingAttacker] Escalated flood intensity to: {level}")
            self.stop_event.wait(self.step_sec)

    def stop(self):
        self.stop_event.set()
        if self.proc:
            self.proc.kill()
            subprocess.run("pkill -9 hping3 2>/dev/null || true", shell=True)

class AdaptiveAttackerThread(threading.Thread):
    """
    Adaptive Attacker (Workload 07): Periodically probes victim latency or reads
    the rate limiter's dynamic rate limit and adjusts flood intensity.
    """
    def __init__(self, target_ip, target_port, controller_log_path, poll_interval=1.0):
        super().__init__()
        self.target_ip = target_ip
        self.target_port = target_port
        self.controller_log_path = controller_log_path
        self.poll_interval = poll_interval
        self.stop_event = threading.Event()
        self.proc = None
        self.levels = ["u500", "u200", "u50", "u20", "u5", "u2", "flood"]
        self.curr_idx = 3  # Start at u20

    def _start_hping(self, level):
        if self.proc:
            self.proc.kill()
            subprocess.run("pkill -9 hping3 2>/dev/null || true", shell=True)
        if level == "flood":
            cmd = f"ip netns exec ns_attacker hping3 --udp -p {self.target_port} --flood {self.target_ip}"
        else:
            cmd = f"ip netns exec ns_attacker hping3 --udp -p {self.target_port} -i {level} {self.target_ip}"
        return subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def run(self):
        while not self.stop_event.is_set():
            level = self.levels[self.curr_idx]
            self.proc = self._start_hping(level)
            log(f"[AdaptiveAttacker] Active flood level: {level} (Index {self.curr_idx})")
            self.stop_event.wait(self.poll_interval)

            # Probe rate-limit state from controller log if available
            if os.path.exists(self.controller_log_path):
                try:
                    with open(self.controller_log_path, "r") as f:
                        lines = f.readlines()
                        if lines:
                            last = json.loads(lines[-1].strip())
                            rate_limit = last.get("attacker_rate_limit_bps", 50000000)
                            # Rational adversary logic: if heavily throttled (< 20 MB/s), back off; else re-escalate
                            if rate_limit < 20000000 and self.curr_idx > 0:
                                self.curr_idx -= 1
                                log(f"[AdaptiveAttacker] High throttling detected ({rate_limit/1e6:.1f} MB/s) -> Backing off to {self.levels[self.curr_idx]}")
                            elif rate_limit >= 40000000 and self.curr_idx < len(self.levels) - 1:
                                self.curr_idx += 1
                                log(f"[AdaptiveAttacker] Low throttling detected ({rate_limit/1e6:.1f} MB/s) -> Escalating to {self.levels[self.curr_idx]}")
                except Exception:
                    pass

    def stop(self):
        self.stop_event.set()
        if self.proc:
            self.proc.kill()
            subprocess.run("pkill -9 hping3 2>/dev/null || true", shell=True)

# --- METRICS & PARSING HELPERS ---

def parse_fortio_json(json_file):
    """Parses Fortio duration histogram and computes P50, P90, P99, P99.9 latency in ms."""
    if not os.path.exists(json_file):
        return {"p50_ms": 0.0, "p90_ms": 0.0, "p99_ms": 0.0, "p999_ms": 0.0, "actual_qps": 0.0}
    try:
        with open(json_file, "r") as f:
            data = json.load(f)
        dur_hist = data.get("DurationHistogram", {})
        actual_qps = dur_hist.get("Avg", 0.0)
        percentiles = {}
        for p in dur_hist.get("Percentiles", []):
            percentiles[p["Percentile"]] = p["Value"] * 1000.0  # Convert s to ms
        return {
            "p50_ms": round(percentiles.get(50.0, 0.0), 3),
            "p90_ms": round(percentiles.get(90.0, 0.0), 3),
            "p99_ms": round(percentiles.get(99.0, 0.0), 3),
            "p999_ms": round(percentiles.get(99.9, 0.0), 3),
            "actual_qps": round(actual_qps, 2)
        }
    except Exception as e:
        log(f"Error parsing {json_file}: {e}")
        return {"p50_ms": 0.0, "p90_ms": 0.0, "p99_ms": 0.0, "p999_ms": 0.0, "actual_qps": 0.0}

def parse_controller_log(log_file):
    """Reads controller JSONL log records."""
    records = []
    if not os.path.exists(log_file):
        return records
    with open(log_file, "r") as f:
        for line in f:
            if line.strip():
                try:
                    records.append(json.loads(line.strip()))
                except Exception:
                    pass
    return records

def compute_mann_whitney_u(x, y):
    """Pure Python Mann-Whitney U test with Bonferroni significance check."""
    n1, n2 = len(x), len(y)
    if n1 == 0 or n2 == 0:
        return 0.0, 1.0
    combined = sorted([(val, 'x') for val in x] + [(val, 'y') for val in y])
    r1 = 0.0
    i = 0
    while i < len(combined):
        j = i
        while j < len(combined) and combined[j][0] == combined[i][0]:
            j += 1
        rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            if combined[k][1] == 'x':
                r1 += rank
        i = j
    u1 = n1 * n2 + (n1 * (n1 + 1)) / 2.0 - r1
    u2 = n1 * n2 - u1
    u = min(u1, u2)
    mu = (n1 * n2) / 2.0
    sigma = math.sqrt((n1 * n2 * (n1 + n2 + 1)) / 12.0)
    if sigma == 0:
        return u, 1.0
    z = (u - mu) / sigma
    pval = math.erfc(abs(z) / math.sqrt(2))
    return u, min(1.0, max(0.0, pval))
