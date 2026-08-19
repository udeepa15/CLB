#!/usr/bin/env python3
"""
run_step3_manual_validation.py — Single Trial Validation for Workloads 03 & 04.

Executes ONE manual trial each (N=1, qos_dynamic architecture) for:
1. Workload 03 (Ramping Escalation)
2. Workload 04 (Flash Crowd Victim Surge)
"""

import os
import sys
import subprocess
from datetime import datetime

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def main():
    if os.geteuid() != 0:
        log("ERROR: Must be run as root (sudo python3 run_step3_manual_validation.py)")
        sys.exit(1)

    base_dir = os.path.dirname(os.path.abspath(__file__))

    log("=== Step 3 Manual Validation: Workload 03 & Workload 04 ===")

    # 1. Test Workload 03 Single Trial
    w3_dir = os.path.join(base_dir, "03_ramping_escalation")
    log("\n--- Validating Workload 03 (Ramping Escalation, N=1, qos_dynamic) ---")
    w3_cmd = f"sudo python3 {os.path.join(w3_dir, 'run_matrix.py')} --reps 1 --architectures qos_dynamic"
    res3 = subprocess.run(w3_cmd, shell=True, cwd=w3_dir)
    if res3.returncode != 0:
        log("CRITICAL ERROR: Workload 03 single trial failed!")
        sys.exit(1)

    # 2. Test Workload 04 Single Trial
    w4_dir = os.path.join(base_dir, "04_flash_crowd_victim")
    log("\n--- Validating Workload 04 (Flash Crowd Victim Surge, N=1, qos_dynamic) ---")
    w4_cmd = f"sudo python3 {os.path.join(w4_dir, 'run_matrix.py')} --reps 1 --architectures qos_dynamic"
    res4 = subprocess.run(w4_cmd, shell=True, cwd=w4_dir)
    if res4.returncode != 0:
        log("CRITICAL ERROR: Workload 04 single trial failed!")
        sys.exit(1)

    log("\nStep 3 Validation Complete! Both Workload 03 and Workload 04 executed successfully.")

if __name__ == "__main__":
    main()
