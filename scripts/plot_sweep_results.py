#!/usr/bin/env python3
"""
Plot sweep results: throughput vs attacker_rate, grouped by tenant_count.
Usage: plot_sweep_results.py --csv sweep_results_v2.csv --output plot_sweep.png
"""
from __future__ import annotations
import argparse
import sys

try:
    import pandas as pd
    import matplotlib.pyplot as plt
    import seaborn as sns
except ImportError as e:
    print(f"Missing dependency: {e}. Install with: pip3 install pandas matplotlib seaborn", file=sys.stderr)
    sys.exit(1)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True, help="Path to aggregated sweep CSV")
    p.add_argument("--output", default="plot_sweep.png", help="Output PNG file")
    return p.parse_args()


def main():
    args = parse_args()
    
    # Read CSV
    try:
        df = pd.read_csv(args.csv)
    except FileNotFoundError:
        print(f"CSV not found: {args.csv}", file=sys.stderr)
        sys.exit(1)
    
    if "mode" not in df.columns:
        df["mode"] = "unknown"

    # Group by mode, tenant_count, attacker_rate
    grouped = df.groupby(["mode", "tenant_count", "attacker_rate"])["throughput_mps"].agg(["mean", "std", "count"]).reset_index()

    # Create figure
    fig, ax = plt.subplots(figsize=(12, 6))

    tenant_values = sorted(grouped["tenant_count"].unique())
    colors = sns.color_palette("tab10", n_colors=len(tenant_values))
    color_map = {t: colors[i] for i, t in enumerate(tenant_values)}
    line_styles = {
        "sidecarless_ebpf": "-",
        "sidecar": "--",
        "unknown": ":"
    }

    for mode in sorted(grouped["mode"].unique()):
        for tenant_count in tenant_values:
            subset = grouped[(grouped["tenant_count"] == tenant_count) & (grouped["mode"] == mode)].sort_values("attacker_rate")
            if subset.empty:
                continue
            ax.plot(
                subset["attacker_rate"],
                subset["mean"],
                marker="o",
                label=f"Tenants={tenant_count} ({mode})",
                linewidth=2,
                markersize=6,
                color=color_map[tenant_count],
                linestyle=line_styles.get(mode, "-")
            )
            if (subset["std"] > 0).any():
                ax.fill_between(
                    subset["attacker_rate"],
                    subset["mean"] - subset["std"],
                    subset["mean"] + subset["std"],
                    alpha=0.15,
                    color=color_map[tenant_count]
                )
    
    ax.set_xlabel("Attacker Rate (msg/sec)", fontsize=12, fontweight="bold")
    ax.set_ylabel("Throughput (msg/sec)", fontsize=12, fontweight="bold")
    ax.set_title("Redis Queue Throughput vs Attacker Rate", fontsize=14, fontweight="bold")
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)
    
    # Save
    plt.tight_layout()
    plt.savefig(args.output, dpi=150)
    print(f"Saved plot to: {args.output}")
    
    # Print summary statistics
    print("\nSummary Statistics:")
    print("=" * 70)
    summary = df.groupby(["mode", "tenant_count"]).agg({
        "throughput_mps": ["min", "max", "mean"],
        "errors": ["sum", "max"],
        "completed": ["sum", "mean"]
    }).round(2)
    print(summary)


if __name__ == "__main__":
    main()
