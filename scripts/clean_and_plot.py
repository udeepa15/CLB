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
DEFAULT_OUTPUT = ROOT / "results" / "cleaned_summary_metrics.csv"
DEFAULT_PLOT = ROOT / "results" / "plots" / "cleaned_p99_tail_latency_ms.png"

ATTACKER_RATES = [0, 10000, 15000, 20000, 25000, 27500, 30000, 32500, 35000, 37500, 40000, 42500, 45000]
MODES = ["baseline", "isolated", "shared"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clean victim-only latency data and regenerate thesis plots."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Source CSV")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Cleaned CSV")
    parser.add_argument("--plot", type=Path, default=DEFAULT_PLOT, help="Output plot path")
    parser.add_argument(
        "--scale",
        choices=("linear", "log"),
        default="linear",
        help="Y-axis scale for the p99 plot",
    )
    return parser.parse_args()


def load_source(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Source CSV not found: {path}")

    df = pd.read_csv(path)
    df = df.copy()
    df["mode"] = df["mode"].astype(str)
    df["attacker_rate"] = pd.to_numeric(df["attacker_rate"], errors="coerce")
    return df


def clean_and_summarize(df: pd.DataFrame) -> pd.DataFrame:
    # FIX 3: Robustly handle polymorphic schemas (victim string vs victim_id int)
    if "victim_id" in df.columns:
        victims = df[(df["victim_id"] > 0) & (df["mode"].isin(MODES))].copy()
    elif "victim" in df.columns:
        victims = df[(df["victim"].astype(str) != "adv") & (df["victim"].astype(str) != "0") & (df["mode"].isin(MODES))].copy()
    else:
        victims = df[df["mode"].isin(MODES)].copy()

    # Normalize metrics column values checking if they are pre-calculated as ms or us
    is_already_ms = "p99_ms" in victims.columns
    p99_col = "p99_ms" if is_already_ms else "p99_us"
    p50_col = "p50_ms" if is_already_ms else "p50_us"
    p95_col = "p95_ms" if is_already_ms else "p95_us"
    p999_col = "p999_ms" if is_already_ms else "p999_us"

    # Eliminate genuine operating system hangs while preserving high-load queueing metrics
    cutoff = 500.0 if is_already_ms else 500000.0
    victims = victims[victims[p99_col] < cutoff]

    # Calculate target scale division factor
    div = 1.0 if is_already_ms else 1000.0

    # Aggregate using MEDIAN to reliably identify structural trends
    grouped = (
        victims.groupby(["mode", "attacker_rate"], as_index=False)
        .agg(
            p50_ms_median=(p50_col, lambda x: x.median() / div),
            p95_ms_median=(p95_col, lambda x: x.median() / div),
            p99_ms_median=(p99_col, lambda x: x.median() / div),
            p999_ms_median=(p999_col, lambda x: x.median() / div),
        )
        .sort_values(["mode", "attacker_rate"])
        .reset_index(drop=True)
    )

    return grouped


def plot_clean_summary(summary: pd.DataFrame, output: Path, scale: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    sns.set_theme(style="whitegrid", context="paper")

    plt.figure(figsize=(8.5, 5.0))
    ax = sns.lineplot(
        data=summary,
        x="attacker_rate",
        y="p99_ms_median",
        hue="mode",
        hue_order=MODES,
        marker="o",
        linewidth=2.5,
    )
    ax.set_xlabel("Attacker Rate (RPS)", fontsize=11, fontweight='bold')
    ax.set_ylabel("P99 Median Tail Latency (ms)", fontsize=11, fontweight='bold')
    ax.set_title("Victim Tenant P99 Performance Isolation Profile", fontsize=13, fontweight='bold', pad=15)
    
    # Dynamically match available ticks present in your dataset
    unique_rates = sorted(summary["attacker_rate"].unique())
    ax.set_xticks(unique_rates)
    plt.xticks(rotation=25)
    
    if scale == "log":
        ax.set_yscale("log")
    
    ax.grid(True, which="both", axis="both", alpha=0.4, linestyle="--")
    ax.legend(title="Architecture Mode", frameon=True, facecolor="white", edgecolor="none")
    plt.tight_layout()
    plt.savefig(output, dpi=300, bbox_inches="tight")
    plt.close()


def main() -> int:
    args = parse_args()
    source = load_source(args.input)
    summary = clean_and_summarize(source)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(args.output, index=False)

    plot_clean_summary(summary, args.plot, args.scale)

    print(f"Wrote cleaned summary to {args.output}")
    print(f"Wrote plot to {args.plot}")
    print(f"Configurations parsed: {len(summary)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())