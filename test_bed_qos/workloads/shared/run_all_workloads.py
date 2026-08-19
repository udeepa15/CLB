#!/usr/bin/env python3
"""
run_all_workloads.py — Top-Level Orchestrator for All 7 Realistic Workload Benchmarks.

Runs all 7 workload benchmarks sequentially:
1. 01_steady_state
2. 02_bursty_oscillating
3. 03_ramping_escalation
4. 04_flash_crowd_victim
5. 05_multi_tenant_mix
6. 06_retry_storm
7. 07_adaptive_attacker

Usage:
    sudo python3 run_all_workloads.py [--reps 10]
"""

import os
import sys
import argparse
import subprocess
from datetime import datetime

WORKLOAD_DIRS = [
    "01_steady_state",
    "02_bursty_oscillating",
    "03_ramping_escalation",
    "04_flash_crowd_victim",
    "05_multi_tenant_mix",
    "06_retry_storm",
    "07_adaptive_attacker"
]

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def main():
    parser = argparse.ArgumentParser(description="Orchestrator for All 7 Workload Benchmarks")
    parser.add_argument("--reps", type=int, default=2, help="Number of repetitions per condition (default: 2)")
    args = parser.parse_args()

    if os.geteuid() != 0:
        log("CRITICAL ERROR: Must be run as root (sudo python3 run_all_workloads.py)")
        sys.exit(1)

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    log("=======================================================================")
    log(f"=== Starting Top-Level Workload Suite Execution (N={args.reps} Reps) ===")
    log("=======================================================================")

    for w_dir in WORKLOAD_DIRS:
        target_path = os.path.join(base_dir, w_dir)
        runner_script = os.path.join(target_path, "run_matrix.py")

        if not os.path.exists(runner_script):
            log(f"WARNING: Runner script {runner_script} not found. Skipping...")
            continue

        log(f"\n=======================================================")
        log(f"=== Executing Workload: {w_dir} ===")
        log(f"=======================================================")

        cmd = f"sudo python3 {runner_script} --reps {args.reps}"
        res = subprocess.run(cmd, shell=True, cwd=target_path)

        if res.returncode != 0:
            log(f"CRITICAL ERROR: Workload {w_dir} execution failed!")
            sys.exit(1)

        # Run workload-specific analysis script
        analysis_script = os.path.join(target_path, "analyze.py")
        if os.path.exists(analysis_script):
            log(f"Running analysis for {w_dir}...")
            subprocess.run(f"python3 {analysis_script}", shell=True, cwd=target_path)

    log("\nAll 7 Workload Benchmarks Completed Successfully!")

if __name__ == "__main__":
    main()
