#!/usr/bin/env python3
"""
analyze.py — Workload 04 Analysis & Phase Comparison Plot Generator.
"""

import os
import sys
import glob
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shared"))
from workload_lib import compute_mann_whitney_u

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    results_dirs = sorted(glob.glob(os.path.join(script_dir, "results", "multi_run_*")))
    if not results_dirs:
        return

    latest_dir = results_dirs[-1]
    raw_csv = os.path.join(latest_dir, "results_summary_raw.csv")
    if not os.path.exists(raw_csv):
        return

    df = pd.read_csv(raw_csv)
    print("=== Workload 04: Flash Crowd Victim Surge Analysis ===")
    print(df.groupby("arch")[["phase1_p99_ms", "phase1_qps", "phase2_p99_ms", "phase2_qps"]].mean())

    plt.figure(figsize=(8, 5))
    melted = df.melt(id_vars=["arch", "rep"], value_vars=["phase1_p99_ms", "phase2_p99_ms"], var_name="Phase", value_name="P99_ms")
    sns.barplot(data=melted, x="Phase", y="P99_ms", hue="arch", palette="Set2")
    plt.title("Workload 04: Flash Crowd Victim Surge (Phase 1 50 QPS vs Phase 2 500 QPS)")
    plt.ylabel("Victim P99 Latency (ms)")
    plots_dir = os.path.join(latest_dir, "plots")
    os.makedirs(plots_dir, exist_ok=True)
    out_plot = os.path.join(plots_dir, "flash_crowd_phase_p99.png")
    plt.savefig(out_plot, dpi=300, bbox_inches="tight")
    plt.close()

if __name__ == "__main__":
    main()
