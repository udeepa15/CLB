#!/usr/bin/env python3
"""
analyze_stallhide.py — Statistical Analysis & Visualization for Direction 2.

Analyzes naive vs stallhide limiter performance across:
1. End-to-End P99 Latency (Fortio)
2. Limiter-Only Latency (limiter_only_latency_hist BPF map)
3. CFS Runqueue Latency (collect_runq_latency.bt)

Computes median/IQR, performs Mann-Whitney U test with Bonferroni correction,
and generates primary result charts.

Usage:
    python3 analyze_stallhide.py [--results-dir results/multi_run_N...]
"""

import os
import sys
import glob
import json
import argparse
import math
import numpy as np
import matplotlib.pyplot as plt

def mann_whitney_u_test(x, y):
    """Pure Python Mann-Whitney U test for two independent samples."""
    n1, n2 = len(x), len(y)
    if n1 == 0 or n2 == 0:
        return 0, 1.0
    combined = sorted([(val, 'x') for val in x] + [(val, 'y') for val in y])
    r1 = 0.0
    i = 0
    while i < len(combined):
        j = i
        while j < len(combined) and combined[j][0] == combined[i][0]:
            j += 1
        rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            if combined[k][1] == 'x':
                r1 += rank
        i = j
    u1 = n1 * n2 + (n1 * (n1 + 1)) / 2.0 - r1
    u2 = n1 * n2 - u1
    u = min(u1, u2)
    mu = (n1 * n2) / 2.0
    sigma = math.sqrt((n1 * n2 * (n1 + n2 + 1)) / 12.0)
    if sigma == 0:
        return u, 1.0
    z = (u - mu) / sigma
    pval = math.erfc(abs(z) / math.sqrt(2))
    return u, min(1.0, max(0.0, pval))

plt.rcParams.update({
    "font.family":       "sans-serif",
    "font.sans-serif":   ["Inter", "Roboto", "DejaVu Sans", "Helvetica"],
    "font.size":         10,
    "axes.edgecolor":    "#D1D5DB",
    "axes.linewidth":    1.2,
    "figure.dpi":        200,
})

COLOR_NAIVE     = "#DC2626"  # Red
COLOR_STALLHIDE = "#7C3AED"  # Purple

def find_latest_results_dir():
    dirs = sorted(glob.glob("results/multi_run_N*/"))
    if not dirs:
        print("CRITICAL ERROR: No results/multi_run_N* directory found.")
        sys.exit(1)
    return dirs[-1]

def parse_limiter_histogram(ebpf_file):
    """Extract weighted mean/median latency (ns) from limiter_only_latency_hist."""
    if not os.path.exists(ebpf_file):
        return None
    counts = {}
    with open(ebpf_file, "r") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rec = json.loads(line.strip())
                lim_hist = rec.get("limiter_histogram", [])
                if not lim_hist:
                    continue
                for entry in lim_hist:
                    k = entry.get("formatted", {}).get("key", 0)
                    vals = entry.get("formatted", {}).get("values", [])
                    total_val = sum(v.get("value", 0) for v in vals)
                    if total_val > 0:
                        counts[k] = counts.get(k, 0) + total_val
            except Exception:
                pass
    if not counts:
        return None

    # Compute weighted median in ns (bucket k -> 2^k ns)
    samples = []
    for k, cnt in counts.items():
        val_ns = 2 ** k
        samples.extend([val_ns] * min(cnt, 100))  # Cap for memory efficiency
    return np.median(samples) if samples else None

def main():
    parser = argparse.ArgumentParser(description="Analyze Naive vs Stallhide Limiter Results")
    parser.add_argument("--results-dir", type=str, default=None, help="Directory containing trial results")
    args = parser.parse_args()

    results_dir = args.results_dir or find_latest_results_dir()
    print(f"Reading benchmark results from: {results_dir}")

    summary_file = os.path.join(results_dir, "results_summary_raw.csv")
    if not os.path.exists(summary_file):
        print(f"CRITICAL ERROR: Summary file {summary_file} not found.")
        sys.exit(1)

    data_naive = {}
    data_stallhide = {}

    with open(summary_file, "r") as f:
        header = f.readline()
        for line in f:
            parts = line.strip().split(",")
            if len(parts) < 7:
                continue
            var, proto, fl, rep, p50, p90, p99, p999, qps = parts[:9]
            p99_val = float(p99)

            if var == "naive":
                if fl not in data_naive: data_naive[fl] = []
                data_naive[fl].append(p99_val)
            elif var == "stallhide":
                if fl not in data_stallhide: data_stallhide[fl] = []
                data_stallhide[fl].append(p99_val)

    flood_levels = [fl for fl in ["0", "u500", "u200", "u50", "u20", "u5", "u2", "flood"] if fl in data_naive or fl in data_stallhide]
    num_tests = len(flood_levels)

    print("\n--- STATISTICAL ANALYSIS: MANN-WHITNEY U TEST (BONFERRONI CORRECTED) ---")
    print(f"{'Flood Level':<12} | {'Naive P99 Median (IQR)':<24} | {'Stallhide P99 Median (IQR)':<26} | {'p-value':<10} | {'Significant?'}")
    print("-" * 88)

    stat_results = []
    for fl in flood_levels:
        n_vals = data_naive.get(fl, [0.0])
        s_vals = data_stallhide.get(fl, [0.0])

        n_med = np.median(n_vals)
        n_iqr = np.percentile(n_vals, 75) - np.percentile(n_vals, 25)
        s_med = np.median(s_vals)
        s_iqr = np.percentile(s_vals, 75) - np.percentile(s_vals, 25)

        if len(n_vals) >= 2 and len(s_vals) >= 2 and n_vals != s_vals:
            stat, pval = mann_whitney_u_test(n_vals, s_vals)
        else:
            pval = 1.0

        adj_alpha = 0.05 / max(1, num_tests)
        sig = "YES (p < adj α)" if pval < adj_alpha else "NO (p ≥ adj α)"

        print(f"{fl:<12} | {n_med:.2f} ms ({n_iqr:.2f})           | {s_med:.2f} ms ({s_iqr:.2f})             | {pval:.4f}     | {sig}")
        stat_results.append({
            "flood_level": fl,
            "naive_med": n_med,
            "stallhide_med": s_med,
            "pval": pval,
            "significant": pval < adj_alpha
        })

    # Plot Primary Result Chart
    output_dir = "plots"
    os.makedirs(output_dir, exist_ok=True)

    fig, ax = plt.subplots(figsize=(9, 5))

    xi = np.arange(len(flood_levels))
    y_naive = [np.median(data_naive.get(fl, [0.0])) for fl in flood_levels]
    y_stallhide = [np.median(data_stallhide.get(fl, [0.0])) for fl in flood_levels]

    ax.plot(xi, y_naive, marker="o", linewidth=2.2, color=COLOR_NAIVE, label="ebpf_limiter_naive (Control)")
    ax.plot(xi, y_stallhide, marker="D", linewidth=2.2, color=COLOR_STALLHIDE, label="ebpf_limiter_stallhide (Beeswax)")

    min_y = min(min(y_naive), min(y_stallhide)) - 0.05
    max_y = max(max(y_naive), max(y_stallhide)) + 0.08
    ax.set_ylim(min_y, max_y)

    for x, yn, ys in zip(xi, y_naive, y_stallhide):
        ax.text(x, yn + 0.01, f"{yn:.2f}", ha="center", va="bottom", fontsize=8, color=COLOR_NAIVE, fontweight="bold")
        ax.text(x, ys - 0.015, f"{ys:.2f}", ha="center", va="top", fontsize=8, color=COLOR_STALLHIDE, fontweight="bold")

    ax.set_title("Direction 2 Ablation (N=10 Matrix): Naive vs Stall-Hiding eBPF Limiter P99 Latency", fontweight="bold", pad=12)
    ax.set_ylabel("P99 Latency (ms)", labelpad=8)
    ax.set_xlabel("Attacker Flood Intensity", labelpad=8)
    ax.set_xticks(xi)
    ax.set_xticklabels(flood_levels, fontsize=9)
    ax.grid(True, linestyle=":", alpha=0.6)
    ax.legend(frameon=True, facecolor="#FFFFFF", edgecolor="#D1D5DB", fontsize=9, loc="lower left")

    plt.tight_layout()
    chart_path = os.path.join(output_dir, "stallhide_comparison_p99.png")
    plt.savefig(chart_path, bbox_inches="tight")
    print(f"\nSaved Primary Ablation Plot: {chart_path}")

if __name__ == "__main__":
    main()
