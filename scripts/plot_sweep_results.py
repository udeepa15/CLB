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
    
    # Group by tenant_count and attacker_rate, average throughput
    grouped = df.groupby(["tenant_count", "attacker_rate"])["throughput_mps"].agg(["mean", "std", "count"]).reset_index()
    
    # Create figure
    fig, ax = plt.subplots(figsize=(12, 6))
    
    # Plot lines for each tenant count
    for tenant_count in sorted(grouped["tenant_count"].unique()):
        subset = grouped[grouped["tenant_count"] == tenant_count].sort_values("attacker_rate")
        ax.plot(
            subset["attacker_rate"],
            subset["mean"],
            marker="o",
            label=f"Tenants={tenant_count}",
            linewidth=2,
            markersize=6
        )
        # Add error bars if std available
        if (subset["std"] > 0).any():
            ax.fill_between(
                subset["attacker_rate"],
                subset["mean"] - subset["std"],
                subset["mean"] + subset["std"],
                alpha=0.2
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
    summary = df.groupby("tenant_count").agg({
        "throughput_mps": ["min", "max", "mean"],
        "errors": ["sum", "max"],
        "completed": ["sum", "mean"]
    }).round(2)
    print(summary)


if __name__ == "__main__":
    main()
