#!/usr/bin/env python3
"""Generate a latency comparison plot from a CSV file."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict

try:
    import matplotlib.pyplot as plt
except ImportError as exc:
    print(f"Missing dependency: {exc}. Install matplotlib to generate the plot.", file=sys.stderr)
    sys.exit(1)


RPS_ORDER = [0, 10000, 20000, 30000, 40000, 50000]
METRICS = [
    ("P50_Latency_ms", "P50 Latency (ms)"),
    ("P90_Latency_ms", "P90 Latency (ms)"),
    ("P99_Latency_ms", "P99 Latency (ms)"),
    ("P999_Latency_ms", "P999 Latency (ms)"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--csv",
        default="results/latency_comparison_synthetic_demo_only.csv",
        help="Input CSV file",
    )
    parser.add_argument(
        "--output",
        default="results/latency_comparison_synthetic_demo_only.png",
        help="Output PNG file",
    )
    return parser.parse_args()


def load_rows(csv_path: str) -> dict[str, dict[int, dict[str, float]]]:
    data: dict[str, dict[int, dict[str, float]]] = defaultdict(dict)
    with open(csv_path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            arch = row["Architecture"].strip()
            rps = int(row["Attacker_RPS"])
            data[arch][rps] = {
                metric: float(row[metric]) for metric, _ in METRICS
            }
    return data


def main() -> int:
    args = parse_args()
    data = load_rows(args.csv)

    plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")
    plt.rcParams.update({
        "font.size": 11,
        "axes.titlesize": 13,
        "axes.labelsize": 11,
        "figure.titlesize": 16,
        "font.family": "sans-serif",
    })

    fig, axes = plt.subplots(2, 2, figsize=(14, 9), sharex=True)
    axes_flat = axes.flatten()
    colors = {
        "sidecar": "#1f77b4",
        "sidecarless": "#d62728",
    }
    labels = {
        "sidecar": "Sidecar",
        "sidecarless": "Sidecarless (synthetic demo)",
    }

    for axis, (metric_key, metric_title) in zip(axes_flat, METRICS):
        for arch in ("sidecar", "sidecarless"):
            if arch not in data:
                continue
            x_values = [rps for rps in RPS_ORDER if rps in data[arch]]
            y_values = [data[arch][rps][metric_key] for rps in x_values]
            axis.plot(
                x_values,
                y_values,
                marker="o",
                linewidth=2.5,
                markersize=6,
                color=colors[arch],
                label=labels[arch],
            )
        axis.set_title(metric_title, fontweight="bold")
        axis.set_xlabel("Attacker RPS")
        axis.set_ylabel("Latency (ms)")
        axis.set_xticks(RPS_ORDER)
        axis.grid(True, linestyle=":", alpha=0.6)

    handles, legend_labels = axes_flat[0].get_legend_handles_labels()
    fig.legend(handles, legend_labels, loc="upper center", ncol=2, frameon=True, bbox_to_anchor=(0.5, 0.98))
    fig.suptitle("Latency Comparison: Sidecar vs Sidecarless", fontweight="bold", y=1.03)
    fig.text(0.5, 0.01, "Synthetic demo dataset for presentation only", ha="center", fontsize=10, style="italic")
    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    plt.savefig(args.output, dpi=200, bbox_inches="tight")
    print(f"Saved plot to: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())