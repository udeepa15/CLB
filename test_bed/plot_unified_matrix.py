#!/usr/bin/env python3
"""
plot_unified_matrix.py — Generate comprehensive comparison plots for Sidecar vs Sidecarless eBPF.

Reads results_summary.csv from the latest (or specified) unified benchmark run directory,
aggregates metrics across victim containers, and generates publication-grade latency & throughput plots.

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
import matplotlib.ticker as ticker

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
    # Fallback to direct results folder
    if os.path.exists("results_summary.csv"):
        return "."
    raise FileNotFoundError("No unified results directory with results_summary.csv found.")


def load_data(summary_path: str) -> pd.DataFrame:
    df = pd.read_csv(summary_path)
    # Convert numerical columns
    for col in ["p50_ms", "p90_ms", "p99_ms", "p999_ms", "actual_qps"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def plot_protocol_comparison(df: pd.DataFrame, output_dir: str):
    """Generate a multi-panel figure comparing P50, P90, P99 across HTTP, TCP, UDP."""
    protocols = [p for p in ["http", "tcp", "udp"] if p in df["protocol"].unique()]
    if not protocols:
        return

    fig, axes = plt.subplots(len(protocols), 3, figsize=(16, 4 * len(protocols)), sharex=True)
    if len(protocols) == 1:
        axes = np.expand_dims(axes, axis=0)

    fig.patch.set_facecolor("#F8FAFC")

    for r, proto in enumerate(protocols):
        proto_df = df[df["protocol"] == proto]
        # Aggregate across victim instances
        agg = proto_df.groupby(["arch", "flood_level"])[["p50_ms", "p90_ms", "p99_ms"]].mean().reset_index()

        for c, metric in enumerate(["p50_ms", "p90_ms", "p99_ms"]):
            ax = axes[r, c]
            ax.set_facecolor("#FFFFFF")

            for arch, color, marker, ls, label in [
                ("sidecar", COLOR_SIDECAR, "s", "--", "Sidecar Proxy"),
                ("sidecarless", COLOR_SIDECARLESS, "o", "-", "Sidecarless eBPF")
            ]:
                arch_data = agg[agg["arch"] == arch]
                y_vals = []
                for fl in FLOOD_ORDER:
                    row = arch_data[arch_data["flood_level"] == fl]
                    val = row[metric].values[0] if not row.empty else np.nan
                    y_vals.append(val)

                ax.plot(
                    range(len(FLOOD_ORDER)), y_vals,
                    marker=marker, linestyle=ls, linewidth=2.2, markersize=7,
                    color=color, label=label
                )

            metric_title = {"p50_ms": "P50 Latency", "p90_ms": "P90 Latency", "p99_ms": "P99 Tail Latency"}[metric]
            ax.set_title(f"{proto.upper()} — {metric_title}", fontweight="bold", pad=8)
            ax.set_ylabel("Latency (ms)", labelpad=6)
            ax.grid(True, linestyle=":", alpha=0.6)
            ax.set_xticks(range(len(FLOOD_ORDER)))
            ax.set_xticklabels(FLOOD_LABELS, fontsize=8)

            if r == 0 and c == 0:
                ax.legend(frameon=True, facecolor="#FFFFFF", edgecolor="#D1D5DB")

    fig.suptitle("Multi-Protocol Benchmarks: Sidecar vs Sidecarless eBPF", fontsize=15, fontweight="bold", y=0.99)
    plt.tight_layout(rect=[0, 0.02, 1, 0.96])

    out_file = os.path.join(output_dir, "unified_protocol_comparison.png")
    plt.savefig(out_file, bbox_inches="tight")
    print(f"Saved: {out_file}")


def plot_http_isolation_deficit(df: pd.DataFrame, output_dir: str):
    """Generate detailed HTTP latency and QPS bar/line comparison."""
    http_df = df[df["protocol"] == "http"]
    if http_df.empty:
        return

    agg = http_df.groupby(["arch", "flood_level"])[["p50_ms", "p90_ms", "p99_ms", "actual_qps"]].mean().reset_index()

    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
    fig.patch.set_facecolor("#F8FAFC")

    # Panel 1: HTTP Latency Comparison
    ax1 = axes[0]
    ax1.set_facecolor("#FFFFFF")
    xi = np.arange(len(FLOOD_ORDER))
    width = 0.35

    sc_p99 = [agg[(agg["arch"] == "sidecar") & (agg["flood_level"] == fl)]["p99_ms"].values[0] if not agg[(agg["arch"] == "sidecar") & (agg["flood_level"] == fl)].empty else 0 for fl in FLOOD_ORDER]
    sl_p99 = [agg[(agg["arch"] == "sidecarless") & (agg["flood_level"] == fl)]["p99_ms"].values[0] if not agg[(agg["arch"] == "sidecarless") & (agg["flood_level"] == fl)].empty else 0 for fl in FLOOD_ORDER]

    b1 = ax1.bar(xi - width/2, sc_p99, width, label="Sidecar Proxy", color=COLOR_SIDECAR, alpha=0.88, edgecolor="white")
    b2 = ax1.bar(xi + width/2, sl_p99, width, label="Sidecarless eBPF", color=COLOR_SIDECARLESS, alpha=0.88, edgecolor="white")

    # Add bar text
    for bar in list(b1) + list(b2):
        h = bar.get_height()
        if np.isfinite(h) and h > 0:
            ax1.text(bar.get_x() + bar.get_width()/2, h + 0.1, f"{h:.1f}", ha="center", va="bottom", fontsize=8, fontweight="bold")

    ax1.set_title("HTTP P99 Tail Latency under Flood", fontweight="bold")
    ax1.set_ylabel("Latency (ms)")
    ax1.set_xticks(xi)
    ax1.set_xticklabels(FLOOD_LABELS, fontsize=8)
    ax1.grid(axis="y", linestyle=":", alpha=0.6)
    ax1.legend(frameon=True)

    # Panel 2: HTTP QPS Throughput
    ax2 = axes[1]
    ax2.set_facecolor("#FFFFFF")
    sc_qps = [agg[(agg["arch"] == "sidecar") & (agg["flood_level"] == fl)]["actual_qps"].values[0] if not agg[(agg["arch"] == "sidecar") & (agg["flood_level"] == fl)].empty else 0 for fl in FLOOD_ORDER]
    sl_qps = [agg[(agg["arch"] == "sidecarless") & (agg["flood_level"] == fl)]["actual_qps"].values[0] if not agg[(agg["arch"] == "sidecarless") & (agg["flood_level"] == fl)].empty else 0 for fl in FLOOD_ORDER]

    ax2.plot(xi, sc_qps, marker="s", linestyle="--", linewidth=2.2, markersize=8, color=COLOR_SIDECAR, label="Sidecar Proxy")
    ax2.plot(xi, sl_qps, marker="o", linestyle="-", linewidth=2.2, markersize=8, color=COLOR_SIDECARLESS, label="Sidecarless eBPF")

    ax2.set_title("HTTP Achieved Throughput (QPS)", fontweight="bold")
    ax2.set_ylabel("QPS")
    ax2.set_xticks(xi)
    ax2.set_xticklabels(FLOOD_LABELS, fontsize=8)
    ax2.grid(True, linestyle=":", alpha=0.6)
    ax2.set_ylim(0, max(max(sc_qps or [60]), max(sl_qps or [60])) * 1.2)
    ax2.legend(frameon=True)

    fig.suptitle("HTTP Performance & Isolation Benchmark", fontsize=15, fontweight="bold", y=0.98)
    plt.tight_layout(rect=[0, 0.02, 1, 0.95])

    out_file = os.path.join(output_dir, "http_isolation_benchmark.png")
    plt.savefig(out_file, bbox_inches="tight")
    print(f"Saved: {out_file}")


def main():
    parser = argparse.ArgumentParser(description="Plot Unified Benchmark Matrix Results")
    parser.add_argument("--results-dir", type=str, default=None, help="Directory containing results_summary.csv")
    args = parser.parse_args()

    results_dir = args.results_dir or find_latest_results_dir()
    summary_path = os.path.join(results_dir, "results_summary.csv")

    if not os.path.exists(summary_path):
        print(f"ERROR: File not found: {summary_path}")
        sys.exit(1)

    print(f"Reading benchmark summary from: {os.path.abspath(summary_path)}")
    df = load_data(summary_path)

    # Output directory for plots (prefer local user-writable plots/)
    plots_dir = "plots"
    os.makedirs(plots_dir, exist_ok=True)

    # Generate figures
    plot_protocol_comparison(df, plots_dir)
    plot_http_isolation_deficit(df, plots_dir)

    # Try copying to results_dir/plots if writable
    try:
        res_plots_dir = os.path.join(results_dir, "plots")
        os.makedirs(res_plots_dir, exist_ok=True)
        import shutil
        for p_file in glob.glob(os.path.join(plots_dir, "*.png")):
            shutil.copy(p_file, res_plots_dir)
    except Exception:
        pass

    print("\nGraph generation complete! Generated plots in plots/")


if __name__ == "__main__":
    main()
