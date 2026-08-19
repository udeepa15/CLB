#!/usr/bin/env python3
"""
run_step6_manual_validation.py — Single Trial Validation for Workload 07 (Rational Adaptive Adversary).

Executes ONE manual trial (N=1, qos_dynamic architecture) for Workload 07.
"""

import os
import sys
import subprocess
from datetime import datetime

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def main():
    if os.geteuid() != 0:
        log("ERROR: Must be run as root (sudo python3 run_step6_manual_validation.py)")
        sys.exit(1)

    base_dir = os.path.dirname(os.path.abspath(__file__))

    log("=== Step 6 Manual Validation: Workload 07 ===")

    w7_dir = os.path.join(base_dir, "07_adaptive_attacker")
    log("\n--- Validating Workload 07 (Adaptive Attacker, N=1, qos_dynamic) ---")
    w7_cmd = f"sudo python3 {os.path.join(w7_dir, 'run_matrix.py')} --reps 1 --architectures qos_dynamic"
    res7 = subprocess.run(w7_cmd, shell=True, cwd=w7_dir)
    if res7.returncode != 0:
        log("CRITICAL ERROR: Workload 07 single trial failed!")
        sys.exit(1)

    log("\nStep 6 Validation Complete! Workload 07 executed successfully.")

if __name__ == "__main__":
    main()
