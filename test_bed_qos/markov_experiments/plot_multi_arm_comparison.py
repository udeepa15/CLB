#!/usr/bin/env python3
"""
plot_multi_arm_comparison.py — Strict, Fully-Empirical Multi-Arm Workload Comparison Plotter.

STRICT INTEGRITY ENFORCEMENT:
- Every data point plotted MUST be computed programmatically from real raw JSON testbed logs.
- NO silent fallbacks, NO default constants, NO synthetic arrays, NO placeholders.
- If raw data is missing or invalid for any arm or workload, this script immediately raises
  an explicit FileNotFoundError or RuntimeError and terminates.
"""

import os
import sys
import glob
import json
import argparse
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

MARKOV_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RESULTS_DIR = os.path.join(MARKOV_DIR, "results")
DEFAULT_GRAPHS_DIR = os.path.join(MARKOV_DIR, "graphs")

# Publication plot styling
plt.rcParams.update({
    'font.size': 11,
    'axes.labelsize': 12,
    'axes.titlesize': 13,
    'xtick.labelsize': 10,
    'ytick.labelsize': 10,
    'legend.fontsize': 10,
    'figure.autolayout': True,
    'pdf.fonttype': 42,
    'ps.fonttype': 42
})

ARM_CONFIGS = {
    "sidecarless": {
        "label": "sidecarless (Baseline eBPF)",
        "color": "#6B7280",      # Gray
        "marker": "^",
        "linestyle": ":",
        "linewidth": 2.0
    },
    "sidecarless_baseline": {
        "label": "sidecarless (Baseline eBPF)",
        "color": "#6B7280",      # Gray
        "marker": "^",
        "linestyle": ":",
        "linewidth": 2.0
    },
    "sidecar": {
        "label": "sidecar (Proxy Reference)",
        "color": "#D97706",      # Amber
        "marker": "D",
        "linestyle": "-.",
        "linewidth": 2.0
    },
    "qos_tiered": {
        "label": "qos_tiered (Static Ceiling)",
        "color": "#DC2626",      # Red
        "marker": "x",
        "linestyle": "-",
        "linewidth": 2.0
    },
    "qos_dynamic": {
        "label": "qos_dynamic (Dynamic Polling)",
        "color": "#2563EB",      # Blue
        "marker": "s",
        "linestyle": "--",
        "linewidth": 2.0
    },
    "qos_dtmc_predictive": {
        "label": "qos_dtmc_predictive (Predictive DTMC)",
        "color": "#7C3AED",      # Purple
        "marker": "o",
        "linestyle": "-",
        "linewidth": 2.2
    }
}


def parse_p99_from_json(file_path):
    """
    Parses Fortio or retry_client JSON and returns P99 latency in milliseconds.
    Raises RuntimeError if file is unreadable, invalid JSON, or lacks P99 data.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"[STRICT AUDIT ERROR] Missing required raw data file: {file_path}")

    try:
        with open(file_path, "r") as f:
            data = json.load(f)
    except Exception as e:
        raise RuntimeError(f"[STRICT AUDIT ERROR] Failed to parse JSON from {file_path}: {e}")

    histogram = data.get("DurationHistogram")
    if not histogram or "Percentiles" not in histogram:
        raise RuntimeError(f"[STRICT AUDIT ERROR] Invalid histogram schema in {file_path}: {list(data.keys())}")

    for p in histogram["Percentiles"]:
        if abs(p.get("Percentile", 0) - 99.0) < 0.1:
            return float(p["Value"]) * 1000.0

    raise RuntimeError(f"[STRICT AUDIT ERROR] P99 percentile not found in {file_path}")


def load_strict_p99_series(results_dir, workload_id, file_pattern, arm_name, expected_min_count=1):
    """
    Finds and parses all JSON files matching pattern.
    Refuses to continue and raises an error if zero files are found.
    """
    raw_dir = os.path.join(results_dir, f"workload_{workload_id:02d}", "raw")
    if not os.path.exists(raw_dir):
        raise FileNotFoundError(f"[STRICT AUDIT ERROR] Raw directory does not exist: {raw_dir} (Arm: {arm_name}, Workload: {workload_id})")

    search_path = os.path.join(raw_dir, file_pattern)
    files = sorted(glob.glob(search_path))

    if not files or len(files) < expected_min_count:
        raise FileNotFoundError(
            f"[STRICT AUDIT ERROR] Insufficient raw logs found for Arm '{arm_name}' in Workload {workload_id:02d}!\n"
            f"  Search pattern: {search_path}\n"
            f"  Found files count: {len(files)}, Required minimum: {expected_min_count}\n"
            f"  Refusing to generate plot with missing empirical data."
        )

    p99_values = []
    for f in files:
        val = parse_p99_from_json(f)
        p99_values.append(val)

    return p99_values


def plot_workload_01(results_dir, graphs_dir, active_arms):
    """Workload 01: Steady-State Baseline across flood levels."""
    print("  -> Plotting Workload 01 (Steady-State Baseline)...")
    flood_levels = ["0", "u200", "u20", "u2", "u1", "flood"]
    x_labels = ["0\n(Clean)", "u200\n(5k pps)", "u20\n(50k pps)", "u2\n(500k pps)", "u1\n(1M pps)", "flood\n(Max)"]
    x = np.arange(len(flood_levels))

    fig, ax = plt.subplots(figsize=(8.5, 5.0))

    for arm in active_arms:
        arm_mean_p99 = []
        for fl in flood_levels:
            pattern = f"fortio_{arm}_flood_{fl}_rep_*.json"
            vals = load_strict_p99_series(results_dir, 1, pattern, arm_name=arm)
            arm_mean_p99.append(float(np.mean(vals)))

        cfg = ARM_CONFIGS[arm]
        ax.plot(x, arm_mean_p99, label=cfg["label"], color=cfg["color"],
                marker=cfg["marker"], linestyle=cfg["linestyle"], linewidth=cfg["linewidth"], markersize=7)

    ax.set_xticks(x)
    ax.set_xticklabels(x_labels)
    ax.set_xlabel("Attacker Intensity (Flood Level)", fontweight="bold")
    ax.set_ylabel("Victim P99 Latency (ms)", fontweight="bold")
    ax.set_title("Workload 01: Steady-State Control Baseline (UDP Flood)", fontweight="bold")
    ax.grid(True, linestyle="--", alpha=0.5)
    ax.legend(loc="upper right", framealpha=0.95)

    out_path = os.path.join(graphs_dir, "01_steady_state_comparison.png")
    plt.savefig(out_path, dpi=300)
    plt.close()
    print(f"     Saved: {out_path}")


def plot_workload_02(results_dir, graphs_dir, active_arms):
    """Workload 02: Bursty Oscillating Attacker Traffic (Aggregate P99 Comparison)."""
    print("  -> Plotting Workload 02 (Bursty Oscillating Traffic)...")
    fig, ax = plt.subplots(figsize=(8.5, 5.0))

    arm_means = []
    arm_stds = []
    arm_labels = []
    arm_colors = []

    for arm in active_arms:
        pattern = f"fortio_{arm}_bursty_rep_*.json"
        vals = load_strict_p99_series(results_dir, 2, pattern, arm_name=arm)
        arm_means.append(float(np.mean(vals)))
        arm_stds.append(float(np.std(vals)))
        cfg = ARM_CONFIGS[arm]
        arm_labels.append(cfg["label"].split(" (")[0])
        arm_colors.append(cfg["color"])

    x = np.arange(len(active_arms))
    rects = ax.bar(x, arm_means, yerr=arm_stds, capsize=5, color=arm_colors, alpha=0.85, edgecolor="black", linewidth=0.8)

    for rect in rects:
        height = rect.get_height()
        ax.annotate(f"{height:.2f}ms",
                    xy=(rect.get_x() + rect.get_width() / 2, height),
                    xytext=(0, 4), textcoords="offset points",
                    ha='center', va='bottom', fontsize=9.0, fontweight='bold')

    ax.set_xticks(x)
    ax.set_xticklabels(arm_labels, fontweight="bold")
    ax.set_ylabel("Victim Aggregate P99 Latency (ms)", fontweight="bold")
    ax.set_title("Workload 02: Bursty Oscillating Attacker (Aggregate P99 Latency)", fontweight="bold")
    ax.grid(True, linestyle="--", alpha=0.5, axis="y")

    out_path = os.path.join(graphs_dir, "02_bursty_oscillating_comparison.png")
    plt.savefig(out_path, dpi=300)
    plt.close()
    print(f"     Saved: {out_path}")


def plot_workload_03(results_dir, graphs_dir, active_arms):
    """Workload 03: Ramping Escalation Attacker Traffic (Aggregate P99 Comparison)."""
    print("  -> Plotting Workload 03 (Ramping Escalation)...")
    fig, ax = plt.subplots(figsize=(8.5, 5.0))

    arm_means = []
    arm_stds = []
    arm_labels = []
    arm_colors = []

    for arm in active_arms:
        pattern = f"fortio_{arm}_ramping_rep_*.json"
        vals = load_strict_p99_series(results_dir, 3, pattern, arm_name=arm)
        arm_means.append(float(np.mean(vals)))
        arm_stds.append(float(np.std(vals)))
        cfg = ARM_CONFIGS[arm]
        arm_labels.append(cfg["label"].split(" (")[0])
        arm_colors.append(cfg["color"])

    x = np.arange(len(active_arms))
    rects = ax.bar(x, arm_means, yerr=arm_stds, capsize=5, color=arm_colors, alpha=0.85, edgecolor="black", linewidth=0.8)

    for rect in rects:
        height = rect.get_height()
        ax.annotate(f"{height:.2f}ms",
                    xy=(rect.get_x() + rect.get_width() / 2, height),
                    xytext=(0, 4), textcoords="offset points",
                    ha='center', va='bottom', fontsize=9.0, fontweight='bold')

    ax.set_xticks(x)
    ax.set_xticklabels(arm_labels, fontweight="bold")
    ax.set_ylabel("Victim Aggregate P99 Latency (ms)", fontweight="bold")
    ax.set_title("Workload 03: Monotonic Ramping Escalation (Aggregate P99 Latency)", fontweight="bold")
    ax.grid(True, linestyle="--", alpha=0.5, axis="y")

    out_path = os.path.join(graphs_dir, "03_ramping_escalation_comparison.png")
    plt.savefig(out_path, dpi=300)
    plt.close()
    print(f"     Saved: {out_path}")


def plot_workload_04(results_dir, graphs_dir, active_arms):
    """Workload 04: Flash Crowd Demand Surge (Phase 1 Baseline vs Phase 2 Surge)."""
    print("  -> Plotting Workload 04 (Flash Crowd Demand Surge)...")
    phases = ["Phase 1: Baseline Load\n(50 QPS)", "Phase 2: Flash Crowd Surge\n(500 QPS)"]
    x = np.arange(len(phases))
    width = 0.8 / max(len(active_arms), 1)

    fig, ax = plt.subplots(figsize=(8.5, 5.0))

    for idx, arm in enumerate(active_arms):
        p1_vals = load_strict_p99_series(results_dir, 4, f"fortio_{arm}_flash_rep_*_p1.json", arm_name=arm)
        p2_vals = load_strict_p99_series(results_dir, 4, f"fortio_{arm}_flash_rep_*_p2.json", arm_name=arm)

        y_vals = [float(np.mean(p1_vals)), float(np.mean(p2_vals))]
        cfg = ARM_CONFIGS[arm]
        offset = (idx - (len(active_arms) - 1) / 2.0) * width
        rects = ax.bar(x + offset, y_vals, width, label=cfg["label"],
                       color=cfg["color"], alpha=0.85, edgecolor="black", linewidth=0.8)

        for rect in rects:
            height = rect.get_height()
            ax.annotate(f"{height:.2f}ms",
                        xy=(rect.get_x() + rect.get_width() / 2, height),
                        xytext=(0, 3), textcoords="offset points",
                        ha='center', va='bottom', fontsize=8.5, fontweight='bold')

    ax.set_xticks(x)
    ax.set_xticklabels(phases, fontweight="bold")
    ax.set_ylabel("Victim P99 Latency (ms)", fontweight="bold")
    ax.set_title("Workload 04: Flash Crowd Demand Surge (HTTP Load)", fontweight="bold")
    ax.grid(True, linestyle="--", alpha=0.5, axis="y")
    ax.legend(loc="upper left", framealpha=0.95)

    out_path = os.path.join(graphs_dir, "04_flash_crowd_comparison.png")
    plt.savefig(out_path, dpi=300)
    plt.close()
    print(f"     Saved: {out_path}")


def plot_workload_05(results_dir, graphs_dir, active_arms):
    """Workload 05: Multi-Tenant Mix (Targeted Tenant 1 vs Co-located Tenant 2)."""
    print("  -> Plotting Workload 05 (Multi-Tenant Isolation)...")
    tenants = ["Targeted Tenant 1\n(50 QPS Demand)", "Neighbor Tenant 2\n(200 QPS Demand)"]
    x = np.arange(len(tenants))
    width = 0.8 / max(len(active_arms), 1)

    fig, ax = plt.subplots(figsize=(8.5, 5.0))

    for idx, arm in enumerate(active_arms):
        vic1_vals = load_strict_p99_series(results_dir, 5, f"fortio_{arm}_multitenant_rep_*_vic1.json", arm_name=arm)
        vic2_vals = load_strict_p99_series(results_dir, 5, f"fortio_{arm}_multitenant_rep_*_vic2.json", arm_name=arm)

        y_vals = [float(np.mean(vic1_vals)), float(np.mean(vic2_vals))]
        cfg = ARM_CONFIGS[arm]
        offset = (idx - (len(active_arms) - 1) / 2.0) * width
        rects = ax.bar(x + offset, y_vals, width, label=cfg["label"],
                       color=cfg["color"], alpha=0.85, edgecolor="black", linewidth=0.8)

        for rect in rects:
            height = rect.get_height()
            ax.annotate(f"{height:.2f}ms",
                        xy=(rect.get_x() + rect.get_width() / 2, height),
                        xytext=(0, 3), textcoords="offset points",
                        ha='center', va='bottom', fontsize=8.5, fontweight='bold')

    ax.set_xticks(x)
    ax.set_xticklabels(tenants, fontweight="bold")
    ax.set_ylabel("Tenant P99 Latency (ms)", fontweight="bold")
    ax.set_title("Workload 05: Multi-Tenant Bleed Isolation", fontweight="bold")
    ax.grid(True, linestyle="--", alpha=0.5, axis="y")
    ax.legend(loc="upper left", framealpha=0.95)

    out_path = os.path.join(graphs_dir, "05_multi_tenant_comparison.png")
    plt.savefig(out_path, dpi=300)
    plt.close()
    print(f"     Saved: {out_path}")


def plot_workload_06(results_dir, graphs_dir, active_arms):
    """Workload 06: Application-Level Retry Storm Stalls."""
    print("  -> Plotting Workload 06 (Retry Storm Amplification)...")
    phases = ["Baseline (0% Failures)", "Retry Storm (20% 503 Overload)"]
    x = np.arange(len(phases))
    width = 0.8 / max(len(active_arms), 1)

    fig, ax = plt.subplots(figsize=(8.5, 5.0))

    for idx, arm in enumerate(active_arms):
        # Baseline traffic from Workload 01 clean flood_0
        baseline_vals = load_strict_p99_series(results_dir, 1, f"fortio_{arm}_flood_0_rep_*.json", arm_name=arm)
        # Retry storm traffic from Workload 06
        retry_vals = load_strict_p99_series(results_dir, 6, f"fortio_{arm}_retry_rep_*.json", arm_name=arm)

        y_vals = [float(np.mean(baseline_vals)), float(np.mean(retry_vals))]
        cfg = ARM_CONFIGS[arm]
        offset = (idx - (len(active_arms) - 1) / 2.0) * width
        rects = ax.bar(x + offset, y_vals, width, label=cfg["label"],
                       color=cfg["color"], alpha=0.85, edgecolor="black", linewidth=0.8)

        for rect in rects:
            height = rect.get_height()
            ax.annotate(f"{height:.1f}ms",
                        xy=(rect.get_x() + rect.get_width() / 2, height),
                        xytext=(0, 3), textcoords="offset points",
                        ha='center', va='bottom', fontsize=8.5, fontweight='bold')

    ax.set_xticks(x)
    ax.set_xticklabels(phases, fontweight="bold")
    ax.set_ylabel("Client-Perceived P99 Latency (ms)", fontweight="bold")
    ax.set_title("Workload 06: Application-Level Retry Storm Stalls", fontweight="bold")
    ax.grid(True, linestyle="--", alpha=0.5, axis="y")
    ax.legend(loc="upper left", framealpha=0.95)

    out_path = os.path.join(graphs_dir, "06_retry_storm_comparison.png")
    plt.savefig(out_path, dpi=300)
    plt.close()
    print(f"     Saved: {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Strict Multi-Arm Empirical Plotter")
    parser.add_argument("--results-dir", type=str, default=DEFAULT_RESULTS_DIR, help="Path to results directory containing workload_01..06 subdirectories")
    parser.add_argument("--graphs-dir", type=str, default=DEFAULT_GRAPHS_DIR, help="Output directory for generated plots")
    parser.add_argument("--arms", nargs="+", default=["sidecarless", "sidecar", "qos_tiered", "qos_dynamic", "qos_dtmc_predictive"],
                        help="List of architectural arms to plot. Must have valid raw JSON logs.")
    args = parser.parse_args()

    for arm in args.arms:
        if arm not in ARM_CONFIGS:
            raise ValueError(f"Unknown architectural arm: {arm}. Supported arms: {list(ARM_CONFIGS.keys())}")

    os.makedirs(args.graphs_dir, exist_ok=True)
    print(f"\n[Strict Empirical Plotter] Checking raw data and generating publication plots...")
    print(f"  Results Source: {args.results_dir}")
    print(f"  Graphs Target:  {args.graphs_dir}")
    print(f"  Active Arms:    {', '.join(args.arms)}\n")

    plot_workload_01(args.results_dir, args.graphs_dir, args.arms)
    plot_workload_02(args.results_dir, args.graphs_dir, args.arms)
    plot_workload_03(args.results_dir, args.graphs_dir, args.arms)
    plot_workload_04(args.results_dir, args.graphs_dir, args.arms)
    plot_workload_05(args.results_dir, args.graphs_dir, args.arms)
    plot_workload_06(args.results_dir, args.graphs_dir, args.arms)

    print(f"\n[SUCCESS] All 6 publication plots successfully generated exclusively from verified empirical logs!\n")


if __name__ == "__main__":
    main()
