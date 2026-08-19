#!/usr/bin/env python3
"""
analyze.py — Workload 02 Analysis & Time-Series Plot Generator.
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
    print("=== Workload 02: Bursty Oscillating Analysis ===")
    print(df.groupby("arch")[["p99_ms", "actual_qps"]].mean())

    tiered = df[df["arch"] == "qos_tiered"]["p99_ms"].tolist()
    dynamic = df[df["arch"] == "qos_dynamic"]["p99_ms"].tolist()
    u_stat, p_val = compute_mann_whitney_u(tiered, dynamic)
    print(f"Mann-Whitney U Test: U={u_stat:.1f}, p={p_val:.4f}")

    plt.figure(figsize=(7, 5))
    sns.boxplot(data=df, x="arch", y="p99_ms", palette="Set2")
    plt.title("Workload 02: Bursty Oscillating Flood P99 Latency")
    plt.ylabel("Victim P99 Latency (ms)")
    plots_dir = os.path.join(latest_dir, "plots")
    os.makedirs(plots_dir, exist_ok=True)
    out_plot = os.path.join(plots_dir, "bursty_p99_box.png")
    plt.savefig(out_plot, dpi=300, bbox_inches="tight")
    plt.close()

if __name__ == "__main__":
    main()
