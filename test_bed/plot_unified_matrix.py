#!/usr/bin/env python3
"""
plot_unified_matrix.py — Focused P99 Tail Latency Plotting for Sidecar vs Sidecarless eBPF.

Reads results_summary.csv from the latest (or specified) unified benchmark run directory,
aggregates P99 metrics across victim containers, and generates publication-grade tail-latency charts.

Usage:
    python3 plot_unified_matrix.py
    python3 plot_unified_matrix.py --results-dir results/unified/20260730_081218
"""

import argparse
import glob
import os
import sys
import pandas as pd
import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ── Global Styling Setup ──────────────────────────────────────────────────────
try:
    plt.style.use("seaborn-v0_8-whitegrid")
except OSError:
    plt.style.use("default")

plt.rcParams.update({
    "font.family":       "sans-serif",
    "font.size":         11,
    "axes.labelsize":    12,
    "axes.titlesize":    13,
    "xtick.labelsize":   10,
    "ytick.labelsize":   10,
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "axes.linewidth":    1.2,
    "figure.dpi":        200,
})

# ── Color Palette ─────────────────────────────────────────────────────────────
COLOR_SIDECAR     = "#2563EB"  # Blue
COLOR_SIDECARLESS = "#DC2626"  # Red / Amber

FLOOD_ORDER = ["0", "u200", "u20", "u2", "u1", "flood"]
FLOOD_LABELS = [
    "Baseline\n(0)",
    "5k pps\n(u200)",
    "50k pps\n(u20)",
    "500k pps\n(u2)",
    "1M pps\n(u1)",
    "Max\n(flood)"
]


def find_latest_results_dir() -> str:
    dirs = sorted(glob.glob("results/unified/*/"))
    for d in reversed(dirs):
        summary_file = os.path.join(d, "results_summary.csv")
        if os.path.exists(summary_file) and os.path.getsize(summary_file) > 0:
            return d.rstrip("/")
    if os.path.exists("results_summary.csv"):
        return "."
    raise FileNotFoundError("No unified results directory with results_summary.csv found.")


def load_data(summary_path: str) -> pd.DataFrame:
    df = pd.read_csv(summary_path)
    for col in ["p50_ms", "p90_ms", "p99_ms", "p999_ms", "actual_qps"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def plot_p99_tail_latency_matrix(df: pd.DataFrame, output_dir: str):
    """Generate a clean side-by-side P99 Tail Latency comparison across HTTP, TCP, and UDP."""
    protocols = [p for p in ["http", "tcp", "udp"] if p in df["protocol"].unique()]
    if not protocols:
        return

    fig, axes = plt.subplots(1, len(protocols), figsize=(5.5 * len(protocols), 5), sharey=False)
    if len(protocols) == 1:
        axes = [axes]

    fig.patch.set_facecolor("#F8FAFC")

    for idx, proto in enumerate(protocols):
        ax = axes[idx]
        ax.set_facecolor("#FFFFFF")

        proto_df = df[df["protocol"] == proto]
        agg = proto_df.groupby(["arch", "flood_level"])["p99_ms"].mean().reset_index()

        xi = np.arange(len(FLOOD_ORDER))

        for arch, color, marker, ls, label in [
            ("sidecar", COLOR_SIDECAR, "s", "--", "Sidecar Proxy"),
            ("sidecarless", COLOR_SIDECARLESS, "o", "-", "Sidecarless eBPF (Map Lock Contention)")
        ]:
            arch_data = agg[agg["arch"] == arch]
            y_vals = []
            for fl in FLOOD_ORDER:
                row = arch_data[arch_data["flood_level"] == fl]
                val = row["p99_ms"].values[0] if not row.empty else np.nan
                y_vals.append(val)

            ax.plot(
                xi, y_vals,
                marker=marker, linestyle=ls, linewidth=2.4, markersize=8,
                color=color, label=label
            )

            # Annotate data points
            for x, y in zip(xi, y_vals):
                if np.isfinite(y):
                    ax.text(x, y + (max(y_vals)*0.02 if max(y_vals)>0 else 0.1), f"{y:.1f}", ha="center", va="bottom", fontsize=8, fontweight="bold", color=color)

        ax.set_title(f"{proto.upper()} Protocol — P99 Tail Latency", fontweight="bold", pad=10)
        ax.set_ylabel("P99 Latency (ms)", labelpad=6)
        ax.set_xlabel("Attacker Flood Intensity", labelpad=8)
        ax.set_xticks(xi)
        ax.set_xticklabels(FLOOD_LABELS, fontsize=8)
        ax.grid(True, linestyle=":", alpha=0.6)
        ax.legend(frameon=True, facecolor="#FFFFFF", edgecolor="#D1D5DB")

    fig.suptitle("Noisy Neighbor Lock Contention: Sidecar vs Sidecarless eBPF (P99 Tail Latency)", fontsize=14, fontweight="bold", y=0.98)
    plt.tight_layout(rect=[0, 0.02, 1, 0.95])

    out_file = os.path.join(output_dir, "p99_tail_latency_matrix.png")
    plt.savefig(out_file, bbox_inches="tight")
    print(f"Saved P99 Latency Plot: {out_file}")


def main():
    parser = argparse.ArgumentParser(description="Plot P99 Tail Latency for Unified Benchmark Matrix Results")
    parser.add_argument("--results-dir", type=str, default=None, help="Directory containing results_summary.csv")
    args = parser.parse_args()

    results_dir = args.results_dir or find_latest_results_dir()
    summary_path = os.path.join(results_dir, "results_summary.csv")

    if not os.path.exists(summary_path):
        print(f"ERROR: File not found: {summary_path}")
        sys.exit(1)

    print(f"Reading benchmark summary from: {os.path.abspath(summary_path)}")
    df = load_data(summary_path)

    plots_dir = "plots"
    os.makedirs(plots_dir, exist_ok=True)

    plot_p99_tail_latency_matrix(df, plots_dir)

    try:
        res_plots_dir = os.path.join(results_dir, "plots")
        os.makedirs(res_plots_dir, exist_ok=True)
        import shutil
        for p_file in glob.glob(os.path.join(plots_dir, "*.png")):
            shutil.copy(p_file, res_plots_dir)
    except Exception:
        pass

    print("\nGraph generation complete! Generated P99 tail latency plots in plots/")


if __name__ == "__main__":
    main()
