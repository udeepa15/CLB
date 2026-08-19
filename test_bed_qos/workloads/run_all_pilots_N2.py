#!/usr/bin/env python3
"""
run_all_pilots_N2.py — Pilot Benchmark Runner (N=2 Repetitions) for All 7 Workloads.

Executes N=2 pilot trials across all 7 workload subdirectories:
1. 01_steady_state (12 trials)
2. 02_bursty_oscillating (4 trials)
3. 03_ramping_escalation (4 trials)
4. 04_flash_crowd_victim (4 trials)
5. 05_multi_tenant_mix (4 trials)
6. 06_retry_storm (4 trials)
7. 07_adaptive_attacker (6 trials — 3 Arms: qos_tiered, qos_proportional, qos_dynamic)

Total Pilot Trials: 38
"""

import os
import sys
import subprocess
from datetime import datetime

WORKLOADS = [
    ("01_steady_state", "01: Extended Steady-State Baseline"),
    ("02_bursty_oscillating", "02: Bursty Oscillating Attacker"),
    ("03_ramping_escalation", "03: Step-Ramping Escalation"),
    ("04_flash_crowd_victim", "04: Flash Crowd Victim Demand Surge"),
    ("05_multi_tenant_mix", "05: Heterogeneous Multi-Tenant Mix"),
    ("06_retry_storm", "06: Unbounded Retry Storm Amplification"),
    ("07_adaptive_attacker", "07: Rational Adaptive Adversary (3-Way Ablation)")
]

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def main():
    if os.geteuid() != 0:
        log("ERROR: Must be run as root (sudo python3 run_all_pilots_N2.py)")
        sys.exit(1)

    base_dir = os.path.dirname(os.path.abspath(__file__))

    log("=======================================================================")
    log("=== Starting Step 7 Pilot Matrix Run (N=2 Reps across 7 Workloads) ===")
    log("=======================================================================")

    for folder_name, desc in WORKLOADS:
        w_dir = os.path.join(base_dir, folder_name)
        runner_script = os.path.join(w_dir, "run_matrix.py")

        log(f"\n=======================================================================")
        log(f"=== Running Pilot Workload {desc} ===")
        log(f"=======================================================================")

        cmd = f"sudo python3 {runner_script} --reps 2"
        res = subprocess.run(cmd, shell=True, cwd=w_dir)
        if res.returncode != 0:
            log(f"CRITICAL ERROR: Pilot execution for {folder_name} failed!")
            sys.exit(1)

    log("\n=======================================================================")
    log("=== Step 7 Pilot Matrix Run (N=2) Completed Successfully! ===")
    log("=======================================================================")

if __name__ == "__main__":
    main()
