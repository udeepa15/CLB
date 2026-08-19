#!/usr/bin/env python3
"""
analyze.py — Workload 01 Statistical Analysis & Plot Generator.

Computes Mann-Whitney U tests (Bonferroni corrected) and generates P99 latency comparison plot.
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
        print("No results directory found in 01_steady_state.")
        return

    latest_dir = results_dirs[-1]
    raw_csv = os.path.join(latest_dir, "results_summary_raw.csv")
    if not os.path.exists(raw_csv):
        print(f"Summary CSV {raw_csv} not found.")
        return

    df = pd.read_csv(raw_csv)
    print("=== Workload 01: Steady-State Analysis ===")
    print(df.groupby(["arch", "flood_level"])[["p99_ms", "actual_qps"]].mean())

    # Statistical Test
    floods = df["flood_level"].unique()
    alpha_adj = 0.05 / max(1, len(floods))
    print(f"\nMann-Whitney U Test Results (Bonferroni adjusted alpha = {alpha_adj:.4f}):")

    for fl in floods:
        sub = df[df["flood_level"] == fl]
        tiered = sub[sub["arch"] == "qos_tiered"]["p99_ms"].tolist()
        dynamic = sub[sub["arch"] == "qos_dynamic"]["p99_ms"].tolist()
        u_stat, p_val = compute_mann_whitney_u(tiered, dynamic)
        sig = "Significant" if p_val < alpha_adj else "Non-Significant (Similar Performance)"
        print(f"  Flood Level {fl}: U={u_stat:.1f}, p={p_val:.4f} -> {sig}")

    # Plot
    plt.figure(figsize=(8, 5))
    sns.barplot(data=df, x="flood_level", y="p99_ms", hue="arch", palette="Set2")
    plt.title("Workload 01: Steady-State 60s P99 Latency Baseline")
    plt.xlabel("Attacker Flood Level")
    plt.ylabel("Victim P99 Latency (ms)")
    plt.grid(True, linestyle="--", alpha=0.5)
    plots_dir = os.path.join(latest_dir, "plots")
    os.makedirs(plots_dir, exist_ok=True)
    out_plot = os.path.join(plots_dir, "steady_state_p99.png")
    plt.savefig(out_plot, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"Plot saved to {out_plot}")

if __name__ == "__main__":
    main()
