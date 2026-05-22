#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent / "ebpf_research"
DEFAULT_INPUT = ROOT / "results" / "cleaned_matrix_metrics.csv"
DEFAULT_PLOT = ROOT / "results" / "plots" / "multi_tenant_isolation_profile.png"

MODES = ["baseline", "isolated", "shared"]
MODE_PALETTE = {"baseline": "#7f8c8d", "isolated": "#2ecc71", "shared": "#e74c3c"}

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate publication-grade multi-tenant line plots.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Cleaned summary CSV")
    parser.add_argument("--output", type=Path, default=DEFAULT_PLOT, help="Output plot image file path")
    return parser.parse_args()

def main() -> int:
    args = parse_args()
    if not args.input.exists():
        print(f"Cleaned metric data summary file not found: {args.input}")
        return 1

    df = pd.read_csv(args.input)
    sns.set_theme(style="whitegrid", context="talk")

    # Construct a FacetGrid mapping columns over distinct tenant density footprints
    g = sns.FacetGrid(
        df,
        col="victim_count",
        hue="mode",
        hue_order=MODES,
        palette=MODE_PALETTE,
        height=5.5,
        aspect=1.0,
        sharey=True
    )
    
    # Overlay the clean trend lines with distinctive data markers
    g.map_dataframe(
        sns.lineplot,
        x="attacker_rate",
        y="p99_ms",
        marker="o",
        markersize=8,
        linewidth=2.5
    )

    # Clean up and labels formatting for publication standards
    g.set_titles(template="{col_name} Active Tenant Node(s)", fontweight="bold", pad=12)
    g.set_axis_labels("Attacker Traffic Rate (RPS)", "P99 Median Tail Latency (ms)", fontweight="bold")
    
    # Configure axes uniformly across panes
    unique_rates = sorted(df["attacker_rate"].unique())
    for ax in g.axes.flat:
        ax.set_xticks([0, 15000, 25000, 35000, 45000])
        ax.grid(True, which="both", linestyle="--", alpha=0.4)
        ax.set_xticklabels(ax.get_xticklabels(), rotation=15, fontsize=10)

    # Add descriptive architecture designations legend layout safely on the side
    g.add_legend(title="Architecture Profile", adjust_subtitles=True)
    plt.subplots_adjust(top=0.82)
    g.fig.suptitle(
        "Cross-Tenant Performance Isolation & Scalability Analysis\nSidecarless Mesh Map-Lock Contention vs. Isolated Footprints",
        fontsize=14,
        fontweight="bold"
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(args.output, dpi=300, bbox_inches="tight")
    plt.close()
    
    print(f"Successfully generated multi-tenant comparison matrix plot to {args.output}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())