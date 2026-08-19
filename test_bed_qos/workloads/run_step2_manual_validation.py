#!/usr/bin/env python3
"""
run_step2_manual_validation.py — Single Trial Validation for Workloads 01 & 02.

Executes ONE manual trial each (N=1, qos_dynamic architecture) for:
1. Workload 01 (Steady-State Control)
2. Workload 02 (Bursty Oscillating)
"""

import os
import sys
import subprocess
from datetime import datetime

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def main():
    if os.geteuid() != 0:
        log("ERROR: Must be run as root (sudo python3 run_step2_manual_validation.py)")
        sys.exit(1)

    base_dir = os.path.dirname(os.path.abspath(__file__))

    log("=== Step 2 Manual Validation: Workload 01 & Workload 02 ===")

    # 1. Test Workload 01 Single Trial
    w1_dir = os.path.join(base_dir, "01_steady_state")
    log("\n--- Validating Workload 01 (Steady-State, N=1, qos_dynamic, u20) ---")
    w1_cmd = f"sudo python3 {os.path.join(w1_dir, 'run_matrix.py')} --reps 1 --architectures qos_dynamic --flood-levels u20"
    res1 = subprocess.run(w1_cmd, shell=True, cwd=w1_dir)
    if res1.returncode != 0:
        log("CRITICAL ERROR: Workload 01 single trial failed!")
        sys.exit(1)

    # 2. Test Workload 02 Single Trial
    w2_dir = os.path.join(base_dir, "02_bursty_oscillating")
    log("\n--- Validating Workload 02 (Bursty Oscillating, N=1, qos_dynamic) ---")
    w2_cmd = f"sudo python3 {os.path.join(w2_dir, 'run_matrix.py')} --reps 1 --architectures qos_dynamic"
    res2 = subprocess.run(w2_cmd, shell=True, cwd=w2_dir)
    if res2.returncode != 0:
        log("CRITICAL ERROR: Workload 02 single trial failed!")
        sys.exit(1)

    log("\nStep 2 Validation Complete! Both Workload 01 and Workload 02 executed successfully.")

if __name__ == "__main__":
    main()
