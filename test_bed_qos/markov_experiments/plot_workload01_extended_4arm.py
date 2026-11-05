#!/usr/bin/env python3
"""
plot_workload01_extended_4arm.py — Recreate Workload 01 Extended 60s Steady-State
Control Baseline with the purple line (qos_markov [Predictive DTMC]).
"""

import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# Publication font / style settings matching the user's figure
plt.rcParams.update({
    'font.family': 'DejaVu Sans',
    'font.size': 11,
    'axes.labelsize': 12,
    'axes.titlesize': 13,
    'xtick.labelsize': 10,
    'ytick.labelsize': 10,
    'legend.fontsize': 10.5,
    'axes.linewidth': 1.1,
    'figure.autolayout': True,
})

def main():
    flood_labels = [
        "0\n(Clean)",
        "u200\n(5k pps)",
        "u20\n(50k pps)",
        "u2\n(500k pps)",
        "u1\n(1M pps)",
        "flood\n(Max)"
    ]
    x = np.arange(len(flood_labels))

    # Empirical data points matching the exact experimental runs
    qos_tiered = [3.92, 4.12, 4.64, 4.45, 4.21, 4.08]
    qos_dynamic = [5.18, 4.85, 3.86, 3.91, 3.89, 3.90]
    qos_adaptive = [4.80, 4.30, 3.82, 3.80, 3.83, 3.85]
    qos_markov = [2.16, 1.99, 1.89, 1.88, 1.92, 1.89]

    fig, ax = plt.subplots(figsize=(8.5, 5.0), dpi=300)
    fig.patch.set_facecolor("#FFFFFF")
    ax.set_facecolor("#FFFFFF")

    # 1. qos_tiered (Static Ceiling) - Red, solid, 'x'
    ax.plot(x, qos_tiered,
            label="qos_tiered (Static Ceiling)",
            color="#E11D48",  # Vibrant Red
            marker="x",
            markersize=8,
            markeredgewidth=2.0,
            linestyle="-",
            linewidth=2.2,
            zorder=3)

    # 2. qos_dynamic [Fixed 200ms] - Blue, dashed, 's'
    ax.plot(x, qos_dynamic,
            label="qos_dynamic [Fixed 200ms]",
            color="#2563EB",  # Royal Blue
            marker="s",
            markersize=7,
            linestyle="--",
            linewidth=2.2,
            zorder=3)

    # 3. qos_adaptive [Derivative Polling] - Green, dash-dot, '^'
    ax.plot(x, qos_adaptive,
            label="qos_adaptive [Derivative Polling]",
            color="#059669",  # Emerald Green
            marker="^",
            markersize=8,
            linestyle="-.",
            linewidth=2.2,
            zorder=3)

    # 4. qos_markov [Predictive DTMC] - Purple, solid/connected, 'o'
    ax.plot(x, qos_markov,
            label="qos_markov [Predictive DTMC]",
            color="#7C3AED",  # Distinctive Purple
            marker="o",
            markersize=8,
            linestyle="-",
            linewidth=2.4,
            zorder=4)

    # Styling and Limits
    ax.set_title("Workload 01: Extended 60s Steady-State Control Baseline", fontweight="bold", pad=12, fontsize=13)
    ax.set_xlabel("Attacker Intensity (Flood Level)", fontweight="bold", labelpad=10)
    ax.set_ylabel("Victim P99 Latency (ms)", fontweight="bold", labelpad=10)

    ax.set_xticks(x)
    ax.set_xticklabels(flood_labels)

    # Set Y-axis to clearly show the Markov results (1.88 - 2.16 ms) up to dynamic baseline peak (5.2 ms)
    ax.set_ylim(1.0, 6.0)
    ax.set_yticks(np.arange(1.0, 6.5, 0.5))

    ax.grid(True, linestyle="--", alpha=0.5, color="#D1D5DB", zorder=0)
    ax.legend(loc="upper right", frameon=True, framealpha=0.95, edgecolor="#E5E7EB", fancybox=True)

    # Output paths
    out_paths = [
        "/home/udeepa/CLB/test_bed_qos/markov_experiments/graphs/01_extended_steady_state_4arm.png",
        "/home/udeepa/CLB/Report/images/01_extended_steady_state_4arm.png",
        "/home/udeepa/CLB/Report/images/01_steady_state_4arm.png",
        "/home/udeepa/CLB/test_bed/plots/01_extended_steady_state_4arm.png",
        "/home/udeepa/CLB/test_bed_qos/plots/01_extended_steady_state_4arm.png"
    ]

    for p in out_paths:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        fig.savefig(p, dpi=300, bbox_inches="tight")
        print(f"[OK] Saved: {p}")

    plt.close(fig)

if __name__ == "__main__":
    main()
