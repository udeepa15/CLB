#!/usr/bin/env python3
"""
run_step5_manual_validation.py — Single Trial Validation for Workload 06 (Retry Storm Amplification).

Executes ONE manual trial (N=1, qos_dynamic architecture) for Workload 06.
"""

import os
import sys
import subprocess
from datetime import datetime

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def main():
    if os.geteuid() != 0:
        log("ERROR: Must be run as root (sudo python3 run_step5_manual_validation.py)")
        sys.exit(1)

    base_dir = os.path.dirname(os.path.abspath(__file__))

    log("=== Step 5 Manual Validation: Workload 06 ===")

    w6_dir = os.path.join(base_dir, "06_retry_storm")
    log("\n--- Validating Workload 06 (Retry Storm, N=1, qos_dynamic) ---")
    w6_cmd = f"sudo python3 {os.path.join(w6_dir, 'run_matrix.py')} --reps 1 --architectures qos_dynamic"
    res6 = subprocess.run(w6_cmd, shell=True, cwd=w6_dir)
    if res6.returncode != 0:
        log("CRITICAL ERROR: Workload 06 single trial failed!")
        sys.exit(1)

    log("\nStep 5 Validation Complete! Workload 06 executed successfully.")

if __name__ == "__main__":
    main()
