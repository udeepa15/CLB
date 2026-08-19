#!/usr/bin/env python3
"""
analyze.py — Workload 05 Analysis & Per-Tenant P99 Bar Chart Generator.
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
    print("=== Workload 05: Multi-Tenant Mix Analysis ===")
    print(df.groupby("arch")[["v1_p99_ms", "v1_qps", "v2_p99_ms", "v2_qps"]].mean())

    plt.figure(figsize=(8, 5))
    melted = df.melt(id_vars=["arch", "rep"], value_vars=["v1_p99_ms", "v2_p99_ms"], var_name="Tenant", value_name="P99_ms")
    sns.barplot(data=melted, x="Tenant", y="P99_ms", hue="arch", palette="Set2")
    plt.title("Workload 05: Multi-Tenant Co-location (Victim 1 Real-time vs Victim 2 Background)")
    plt.ylabel("Tenant P99 Latency (ms)")
    plots_dir = os.path.join(latest_dir, "plots")
    os.makedirs(plots_dir, exist_ok=True)
    out_plot = os.path.join(plots_dir, "multitenant_per_tenant_p99.png")
    plt.savefig(out_plot, dpi=300, bbox_inches="tight")
    plt.close()

if __name__ == "__main__":
    main()
