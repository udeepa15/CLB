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
DEFAULT_INPUT = ROOT / "sidecar_vs_sidecarless_metrics.csv"
DEFAULT_OUTPUT = ROOT / "results" / "cleaned_summary_metrics.csv"
DEFAULT_PLOT = ROOT / "results" / "plots" / "cleaned_p99_tail_latency_ms.png"

ATTACKER_RATES = [0, 15000, 25000, 35000, 45000]
MODES = ["baseline", "isolated", "shared"]
US_COLUMNS = ["p50_us", "p95_us", "p99_us", "p999_us"]


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
    required = {"mode", "attacker_rate", "victim", *US_COLUMNS}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"CSV missing required columns: {sorted(missing)}")

    df = df.copy()
    df["mode"] = df["mode"].astype(str)
    df["victim"] = df["victim"].astype(str)
    df["attacker_rate"] = pd.to_numeric(df["attacker_rate"], errors="coerce")
    for column in US_COLUMNS + ["actual_qps", "target_qps"]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def clean_and_summarize(df: pd.DataFrame) -> pd.DataFrame:
    # 1. Filter out attacker records
    victims = df[(df["victim"] != "adv") & (df["mode"].isin(MODES))].copy()
    victims = victims[victims["attacker_rate"].isin(ATTACKER_RATES)]
    victims = victims.dropna(subset=["attacker_rate"])

    # 2. FILTER RUNTIME OUTLIERS: Eliminate severe container cold-starts and infrastructure stalls
    # (> 5.0ms under zero load, or > 13.0ms under mid-tier load) that corrupt structural profiling
    victims = victims[~(
        ((victims["attacker_rate"] == 0) & (victims["p99_us"] > 5000)) |
        ((victims["attacker_rate"] > 0) & (victims["p99_us"] > 13000))
    )]

    # 3. Aggregate using MEDIAN to reliably identify the central performance trend
    grouped = (
        victims.groupby(["mode", "attacker_rate"], as_index=False)
        .agg(
            victim_rows=("victim", "count"),
            p50_us_med=("p50_us", "median"),
            p95_us_med=("p95_us", "median"),
            p99_us_med=("p99_us", "median"),
            p999_us_med=("p999_us", "median"),
            actual_qps_mean=("actual_qps", "mean"),
        )
        .sort_values(["mode", "attacker_rate"])
        .reset_index(drop=True)
    )

    # Convert to milliseconds
    for column in ["p50_us_med", "p95_us_med", "p99_us_med", "p999_us_med"]:
        grouped[column] = grouped[column] / 1000.0

    grouped = grouped.rename(
        columns={
            "p50_us_med": "p50_ms_median",
            "p95_us_med": "p95_ms_median",
            "p99_us_med": "p99_ms_median",
            "p999_us_med": "p999_ms_median",
        }
    )

    ordered_columns = [
        "mode",
        "attacker_rate",
        "victim_rows",
        "p50_ms_median",
        "p95_ms_median",
        "p99_ms_median",
        "p999_ms_median",
        "actual_qps_mean",
    ]
    return grouped[ordered_columns]


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
    ax.set_xticks(ATTACKER_RATES)
    
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
    print(f"Rows kept: {len(summary)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())