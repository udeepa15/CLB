#!/usr/bin/env python3
"""
run_step4_manual_validation.py — Single Trial Validation for Workload 05 (Multi-Tenant Mix).

Executes ONE manual trial (N=1, qos_dynamic architecture) for Workload 05.
"""

import os
import sys
import subprocess
from datetime import datetime

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def main():
    if os.geteuid() != 0:
        log("ERROR: Must be run as root (sudo python3 run_step4_manual_validation.py)")
        sys.exit(1)

    base_dir = os.path.dirname(os.path.abspath(__file__))

    log("=== Step 4 Manual Validation: Workload 05 ===")

    w5_dir = os.path.join(base_dir, "05_multi_tenant_mix")
    log("\n--- Validating Workload 05 (Multi-Tenant Mix, N=1, qos_dynamic) ---")
    w5_cmd = f"sudo python3 {os.path.join(w5_dir, 'run_matrix.py')} --reps 1 --architectures qos_dynamic"
    res5 = subprocess.run(w5_cmd, shell=True, cwd=w5_dir)
    if res5.returncode != 0:
        log("CRITICAL ERROR: Workload 05 single trial failed!")
        sys.exit(1)

    log("\nStep 4 Validation Complete! Workload 05 executed successfully.")

if __name__ == "__main__":
    main()
