#!/usr/bin/env python3
"""
plot_multi_run_matrix.py — 4-Architecture P99 Latency Plotter & Controller Timeline Visualization.

1. Parses results_summary_avg.csv and generates side-by-side P99 comparison charts for:
   - sidecarless (Baseline eBPF)
   - sidecar     (Proxy reference)
   - qos_tiered  (Static priority tiering)
   - qos_dynamic (Dynamic Stackelberg rate limiting)
2. Parses qos_controller_log.jsonl files and generates an overlay timeline chart:
   - Controller Attacker Rate Limit (bps) vs eBPF Update Hit Rate (hits/sec).

Usage:
    python3 plot_multi_run_matrix.py
    python3 plot_multi_run_matrix.py --results-dir results/multi_run_N2_<timestamp>
"""

import argparse
import glob
import json
import os
import sys
import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Global Styling Setup
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

# 4-Architecture Color Palette
COLOR_SIDECAR     = "#2563EB"  # Blue
COLOR_SIDECARLESS = "#DC2626"  # Red
COLOR_QOS_TIERED  = "#059669"  # Green
COLOR_QOS_DYNAMIC = "#7C3AED"  # Purple

FLOOD_ORDER = ["0", "u500", "u200", "u50", "u20", "u5", "u2"]
FLOOD_LABELS = [
    "Baseline\n(0)",
    "2k pps\n(u500)",
    "5k pps\n(u200)",
    "20k pps\n(u50)",
    "50k pps\n(u20)",
    "200k pps\n(u5)",
    "500k pps\n(u2)"
]


def find_latest_multi_run_dir() -> str:
    dirs = sorted(glob.glob("results/multi_run_N*/*/")) + sorted(glob.glob("results/multi_run_N*/"))
    for d in reversed(dirs):
        summary_file = os.path.join(d, "results_summary_avg.csv")
        if os.path.exists(summary_file) and os.path.getsize(summary_file) > 0:
            return d.rstrip("/")
    if os.path.exists("results_summary_avg.csv"):
        return "."
    raise FileNotFoundError("No multi-run results directory with results_summary_avg.csv found.")


def load_data(summary_path: str) -> pd.DataFrame:
    df = pd.read_csv(summary_path)
    for col in ["p50_ms", "p90_ms", "p99_ms", "p999_ms", "actual_qps"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def plot_p99_tail_latency_matrix(df: pd.DataFrame, output_dir: str):
    """Generate 4-Architecture P99 Tail Latency comparison across protocols."""
    target_protocols = ["grpc", "tcp", "http"]
    protocols = [p for p in target_protocols if p in df["protocol"].unique()]
    for p in df["protocol"].unique():
        if p not in protocols:
            protocols.append(p)

    if not protocols:
        print("No protocols found in dataset.")
        return

    fig, axes = plt.subplots(1, len(protocols), figsize=(6.0 * len(protocols), 5), sharey=False)
    if len(protocols) == 1:
        axes = [axes]

    fig.patch.set_facecolor("#F8FAFC")

    # Available flood levels in dataset
    avail_floods = [fl for fl in FLOOD_ORDER if fl in df["flood_level"].unique()]
    avail_labels = [FLOOD_LABELS[FLOOD_ORDER.index(fl)] for fl in avail_floods]
    xi = np.arange(len(avail_floods))

    for idx, proto in enumerate(protocols):
        ax = axes[idx]
        ax.set_facecolor("#FFFFFF")

        proto_df = df[df["protocol"] == proto]
        agg = proto_df.groupby(["arch", "flood_level"])["p99_ms"].mean().reset_index()

        arch_configs = [
            ("sidecarless", COLOR_SIDECARLESS, "o", "-", "Sidecarless eBPF Baseline"),
            ("sidecar", COLOR_SIDECAR, "s", "--", "Sidecar Proxy Reference"),
            ("qos_tiered", COLOR_QOS_TIERED, "^", "-.", "QoS Tiered (Terway L0/L1/L2)"),
            ("qos_dynamic", COLOR_QOS_DYNAMIC, "D", "-", "QoS Dynamic (Stackelberg)"),
        ]

        for arch, color, marker, ls, label in arch_configs:
            arch_data = agg[agg["arch"] == arch]
            if arch_data.empty:
                continue
            y_vals = []
            for fl in avail_floods:
                row = arch_data[arch_data["flood_level"] == fl]
                val = row["p99_ms"].values[0] if not row.empty else np.nan
                y_vals.append(val)

            ax.plot(
                xi, y_vals,
                marker=marker, linestyle=ls, linewidth=2.2, markersize=7,
                color=color, label=label
            )

            for x, y in zip(xi, y_vals):
                if np.isfinite(y):
                    ax.text(x, y + 0.1, f"{y:.1f}", ha="center", va="bottom", fontsize=7.5, fontweight="bold", color=color)

        ax.set_title(f"{proto.upper()} Protocol — P99 Latency (N=2/N=5)", fontweight="bold", pad=10)
        ax.set_ylabel("P99 Latency (ms)", labelpad=6)
        ax.set_xlabel("Attacker Flood Intensity", labelpad=8)
        ax.set_xticks(xi)
        ax.set_xticklabels(avail_labels, fontsize=8)
        ax.grid(True, linestyle=":", alpha=0.6)
        ax.legend(frameon=True, facecolor="#FFFFFF", edgecolor="#D1D5DB", fontsize=8.5)

    fig.suptitle("4-Architecture QoS Comparison: Sidecarless vs Sidecar vs Tiered vs Dynamic Stackelberg", fontsize=13, fontweight="bold", y=0.98)
    plt.tight_layout(rect=[0, 0.02, 1, 0.95])

    out_file = os.path.join(output_dir, "p99_multi_run_avg.png")
    plt.savefig(out_file, bbox_inches="tight")
    print(f"Saved 4-Architecture P99 Latency Plot: {out_file}")


def plot_qos_dynamic_timeline(results_dir: str, output_dir: str):
    """Plot Stackelberg Controller adjustment timeline overlaid against eBPF hit rate signals."""
    log_files = glob.glob(os.path.join(results_dir, "raw", "qos_controller_*.jsonl")) + glob.glob(os.path.join(results_dir, "*.jsonl"))
    if not log_files:
        print("No qos_controller_log.jsonl files found for timeline plot.")
        return

    # Prefer flood_flood or flood_u20 trial log over baseline flood_0 log
    log_path = log_files[0]
    for lf in log_files:
        if "flood_flood" in lf:
            log_path = lf
            break
        elif "flood_u20" in lf:
            log_path = lf

    records = []
    with open(log_path, "r") as f:
        for line in f:
            if line.strip():
                try:
                    records.append(json.loads(line.strip()))
                except Exception:
                    pass

    if not records:
        print("Empty qos_controller log records.")
        return

    df_ctrl = pd.DataFrame(records)
    if "timestamp" not in df_ctrl.columns or "hits_per_sec" not in df_ctrl.columns:
        return

    t0 = df_ctrl["timestamp"].min()
    df_ctrl["rel_time"] = df_ctrl["timestamp"] - t0
    df_ctrl["rate_mbps"] = df_ctrl["attacker_rate_limit_bps"] / 1000000.0

    fig, ax1 = plt.subplots(figsize=(9, 4.5))
    ax1.set_facecolor("#FFFFFF")
    fig.patch.set_facecolor("#F8FAFC")

    # Plot eBPF Hit Rate Signal (Left Axis)
    line1 = ax1.plot(df_ctrl["rel_time"], df_ctrl["hits_per_sec"], color="#DC2626", linestyle="-", linewidth=2.0, label="eBPF Hit Rate (hits/sec)")
    ax1.set_xlabel("Trial Time (seconds)", labelpad=8)
    ax1.set_ylabel("eBPF Update Hit Rate (hits/sec)", color="#DC2626", fontweight="bold")
    ax1.tick_params(axis="y", labelcolor="#DC2626")
    ax1.grid(True, linestyle=":", alpha=0.6)

    # Plot Controller Attacker Rate Limit (Right Axis)
    ax2 = ax1.twinx()
    line2 = ax2.plot(df_ctrl["rel_time"], df_ctrl["rate_mbps"], color="#7C3AED", linestyle="--", linewidth=2.2, label="Stackelberg Attacker Rate Limit (MB/s)")
    ax2.set_ylabel("Attacker Rate Limit (MB/s)", color="#7C3AED", fontweight="bold")
    ax2.tick_params(axis="y", labelcolor="#7C3AED")

    lines = line1 + line2
    labels = [l.get_label() for l in lines]
    ax1.legend(lines, labels, loc="upper right", frameon=True, facecolor="#FFFFFF", edgecolor="#D1D5DB")

    plt.title("Stackelberg Controller Real-Time Reaction to eBPF Contention Signals", fontweight="bold", pad=12)
    plt.tight_layout()

    out_file = os.path.join(output_dir, "qos_dynamic_controller_timeline.png")
    plt.savefig(out_file, bbox_inches="tight")
    print(f"Saved Stackelberg Controller Timeline Plot: {out_file}")


def main():
    parser = argparse.ArgumentParser(description="Plot 4-Architecture P99 Latency & Stackelberg Controller Timeline")
    parser.add_argument("--results-dir", type=str, default=None, help="Directory containing results_summary_avg.csv")
    args = parser.parse_args()

    results_dir = args.results_dir or find_latest_multi_run_dir()
    summary_path = os.path.join(results_dir, "results_summary_avg.csv")

    if not os.path.exists(summary_path):
        print(f"ERROR: File not found: {summary_path}")
        sys.exit(1)

    print(f"Reading 4-architecture benchmark summary from: {os.path.abspath(summary_path)}")
    df = load_data(summary_path)

    plots_dir = "plots"
    os.makedirs(plots_dir, exist_ok=True)

    plot_p99_tail_latency_matrix(df, plots_dir)
    plot_qos_dynamic_timeline(results_dir, plots_dir)

    try:
        res_plots_dir = os.path.join(results_dir, "plots")
        os.makedirs(res_plots_dir, exist_ok=True)
        import shutil
        for p_file in glob.glob(os.path.join(plots_dir, "*.png")):
            shutil.copy(p_file, res_plots_dir)
    except Exception:
        pass

    print("\nPlotting complete! Generated plots/p99_multi_run_avg.png and plots/qos_dynamic_controller_timeline.png")


if __name__ == "__main__":
    main()
