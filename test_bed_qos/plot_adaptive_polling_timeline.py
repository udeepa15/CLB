#!/usr/bin/env python3
"""
plot_adaptive_polling_timeline.py — Dual-Axis Timeline Visualization for Adaptive Controller Polling.

Visualizes the real-time interaction between Kernel Lock Contention / Rate Limit decisions and
the Controller's Adaptive Polling Interval (polling_interval_ms).

Dual-Axis Layout:
- Left Y-Axis: Kernel Lock Contention Metric (hits/sec) & Attacker Rate Limit (MB/s).
- Right Y-Axis: Polling Interval (ms), plotted as a step graph (20ms spike, 200ms baseline, 1000ms relaxed).

Usage:
    python3 plot_adaptive_polling_timeline.py --log-file results/.../qos_controller_log.jsonl --output plots/adaptive_polling_timeline.png
"""

import argparse
import glob
import json
import os
import sys
import pandas as pd
import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Global Matplotlib Styling
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


def find_latest_controller_log() -> str:
    candidates = (
        sorted(glob.glob("results/multi_run_N*/**/qos_controller_*.jsonl", recursive=True)) +
        sorted(glob.glob("results/**/qos_controller_*.jsonl", recursive=True)) +
        sorted(glob.glob("**/qos_controller_log.jsonl", recursive=True))
    )
    for c in reversed(candidates):
        if os.path.exists(c) and os.path.getsize(c) > 0:
            return c
    return "qos_controller_log.jsonl"


def load_controller_log(log_path: str) -> pd.DataFrame:
    records = []
    with open(log_path, "r") as f:
        for line in f:
            if line.strip():
                try:
                    records.append(json.loads(line.strip()))
                except Exception:
                    pass
    if not records:
        raise ValueError(f"No valid JSON records found in {log_path}")
    
    df = pd.DataFrame(records)
    t0 = df["timestamp"].min()
    df["rel_time"] = df["timestamp"] - t0
    df["rate_mbps"] = df["attacker_rate_limit_bps"] / 1000000.0
    if "polling_interval_ms" not in df.columns:
        df["polling_interval_ms"] = (df["dt_sec"] * 1000).round().astype(int)
    return df


def plot_adaptive_timeline(df: pd.DataFrame, output_path: str):
    fig, ax1 = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor("#F8FAFC")
    ax1.set_facecolor("#FFFFFF")

    # Left Y-Axis: Kernel Lock Contention (hits/sec) & Attacker Rate Limit (MB/s)
    line_hits = ax1.plot(
        df["rel_time"], df["hits_per_sec"],
        color="#DC2626", linestyle="-", linewidth=2.0,
        label="Kernel Lock Contention (hits/sec)"
    )
    line_rate = ax1.plot(
        df["rel_time"], df["rate_mbps"],
        color="#7C3AED", linestyle="--", linewidth=1.8,
        label="Attacker Rate Limit $r_i$ (MB/s)"
    )

    ax1.set_xlabel("Trial Timeline (seconds)", labelpad=8, fontweight="bold")
    ax1.set_ylabel("Lock Contention (hits/sec) / Rate Limit (MB/s)", color="#1E293B", fontweight="bold")
    ax1.tick_params(axis="y", labelcolor="#1E293B")
    ax1.grid(True, linestyle=":", alpha=0.6)

    # Right Y-Axis: Adaptive Polling Interval (ms) step graph
    ax2 = ax1.twinx()
    line_poll = ax2.plot(
        df["rel_time"], df["polling_interval_ms"],
        color="#2563EB", linestyle="-", linewidth=2.2, drawstyle="steps-post",
        label="Controller Polling Interval (ms)"
    )
    ax2.set_ylabel("Polling Interval (ms)", color="#2563EB", fontweight="bold")
    ax2.tick_params(axis="y", labelcolor="#2563EB")
    ax2.set_yscale("log")
    ax2.set_yticks([20, 200, 1000])
    ax2.get_yaxis().set_major_formatter(matplotlib.ticker.ScalarFormatter())

    # Add reference boundary lines for polling thresholds
    ax2.axhline(y=20, color="#DC2626", linestyle=":", alpha=0.4, label="Spike Boundary (20ms)")
    ax2.axhline(y=200, color="#64748B", linestyle=":", alpha=0.4, label="Baseline Boundary (200ms)")
    ax2.axhline(y=1000, color="#059669", linestyle=":", alpha=0.4, label="Calm Boundary (1000ms)")

    # Combine Legend
    lines = line_hits + line_rate + line_poll
    labels = [l.get_label() for l in lines]
    ax1.legend(lines, labels, loc="upper right", frameon=True, facecolor="#FFFFFF", edgecolor="#D1D5DB", fontsize=8.5)

    plt.title("Adaptive Control Plane Polling Interval vs Kernel Lock Contention Spikes", fontweight="bold", pad=12)
    plt.tight_layout()

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    plt.savefig(output_path, bbox_inches="tight")
    print(f"Successfully generated adaptive polling timeline plot: {os.path.abspath(output_path)}")


def main():
    parser = argparse.ArgumentParser(description="Plot Adaptive Controller Polling Interval Timeline")
    parser.add_argument("--log-file", type=str, default=None, help="Path to qos_controller_log.jsonl")
    parser.add_argument("--output", type=str, default="plots/adaptive_polling_timeline.png", help="Path to save output plot PNG")
    args = parser.parse_args()

    log_file = args.log_file or find_latest_controller_log()
    print(f"Reading controller log file from: {os.path.abspath(log_file)}")

    try:
        df = load_controller_log(log_file)
        plot_adaptive_timeline(df, args.output)
    except Exception as e:
        print(f"Error plotting timeline: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
