#!/usr/bin/env python3
"""
plot_presentation_4arm_beeswax.py — 4-Architecture Presentation Plot:
Sidecarless Baseline vs Beeswax vs Sidecar vs QoS Tiered.
"""

import os
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

# Presentation-Grade Styling
plt.rcParams.update({
    "font.family":       "sans-serif",
    "font.sans-serif":   ["Inter", "DejaVu Sans", "Helvetica", "Arial"],
    "font.size":         13,
    "axes.labelsize":    15,
    "axes.titlesize":    16,
    "xtick.labelsize":   13,
    "ytick.labelsize":   13,
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "axes.linewidth":    1.5,
    "figure.dpi":        300,
})

COLOR_SIDECARLESS = "#DC2626"  # Crimson Red
COLOR_BEESWAX     = "#7C3AED"  # Purple
COLOR_SIDECAR     = "#2563EB"  # Royal Blue
COLOR_TIERED      = "#059669"  # Emerald Green
COLOR_BG          = "#FAFAFA"  # Off-White Canvas
COLOR_GRID        = "#E5E7EB"  # Soft Grid

FLOOD_LABELS = [
    "Baseline\n(0 pps)",
    "2k pps\n(u500)",
    "5k pps\n(u200)",
    "20k pps\n(u50)",
    "50k pps\n(u20)",
    "200k pps\n(u5)",
    "500k pps\n(u2)"
]

def main():
    xi = np.arange(len(FLOOD_LABELS))

    y_sl = [5.2, 8.1, 11.2, 11.9, 11.0, 14.5, 11.8]
    y_bw = [5.2, 8.1, 11.2, 11.24, 11.28, 14.88, 11.8]
    y_sc = [4.2, 3.9, 6.1, 5.6, 4.2, 4.3, 4.0]
    y_ti = [4.8, 4.1, 4.2, 4.0, 4.5, 4.2, 4.2]

    fig, ax = plt.subplots(figsize=(14, 7.5))
    fig.patch.set_facecolor(COLOR_BG)
    ax.set_facecolor("#FFFFFF")

    # 1. Sidecarless Baseline (Red, solid, 'o')
    ax.plot(
        xi, y_sl,
        marker="o", markersize=10, linewidth=3.2,
        color=COLOR_SIDECARLESS, label="Sidecarless eBPF Baseline (No QoS)",
        zorder=5
    )

    # 2. Beeswax Stall-Hiding (Purple, dashed, 'D')
    ax.plot(
        xi, y_bw,
        marker="D", markersize=8, linewidth=2.4, linestyle="--",
        color=COLOR_BEESWAX, label="Sidecarless Beeswax (Stall-Hiding eBPF)",
        zorder=6
    )

    # 3. Sidecar Reference (Blue, dashed, 's')
    ax.plot(
        xi, y_sc,
        marker="s", markersize=9, linewidth=2.6, linestyle=":",
        color=COLOR_SIDECAR, label="Sidecar Proxy Reference (Socat/Envoy)",
        zorder=4
    )

    # 4. QoS Tiered (Green, dash-dot, '^')
    ax.plot(
        xi, y_ti,
        marker="^", markersize=10, linewidth=3.0, linestyle="-.",
        color=COLOR_TIERED, label="QoS Tiered (Terway Static eBPF Priority)",
        zorder=7
    )

    # Annotate badges for high points
    for x in xi:
        ys, yb, yc, yt = y_sl[x], y_bw[x], y_sc[x], y_ti[x]
        if x in [3, 4, 5]: # show distinct Beeswax / Naive values
            if x == 5:
                ax.annotate(f"{ys:.1f}", xy=(x, ys), xytext=(-22, -15), textcoords="offset points",
                            ha="center", fontsize=9.5, fontweight="bold", color=COLOR_SIDECARLESS,
                            bbox=dict(boxstyle="round,pad=0.2", facecolor="#FEE2E2", edgecolor="none", alpha=0.9))
                ax.annotate(f"{yb:.1f}", xy=(x, yb), xytext=(22, 10), textcoords="offset points",
                            ha="center", fontsize=9.5, fontweight="bold", color=COLOR_BEESWAX,
                            bbox=dict(boxstyle="round,pad=0.2", facecolor="#F3E8FF", edgecolor="none", alpha=0.9))
            elif x == 3:
                ax.annotate(f"{ys:.1f}", xy=(x, ys), xytext=(0, 10), textcoords="offset points",
                            ha="center", fontsize=9.5, fontweight="bold", color=COLOR_SIDECARLESS,
                            bbox=dict(boxstyle="round,pad=0.2", facecolor="#FEE2E2", edgecolor="none", alpha=0.9))
                ax.annotate(f"{yb:.1f}", xy=(x, yb), xytext=(0, -16), textcoords="offset points",
                            ha="center", fontsize=9.5, fontweight="bold", color=COLOR_BEESWAX,
                            bbox=dict(boxstyle="round,pad=0.2", facecolor="#F3E8FF", edgecolor="none", alpha=0.9))
        else:
            ax.annotate(f"{ys:.1f}", xy=(x, ys), xytext=(0, 10), textcoords="offset points",
                        ha="center", fontsize=9.5, fontweight="bold", color=COLOR_SIDECARLESS,
                        bbox=dict(boxstyle="round,pad=0.2", facecolor="#FEE2E2", edgecolor="none", alpha=0.9))

        # QoS Tiered badges
        ax.annotate(f"{yt:.1f}", xy=(x, yt), xytext=(0, 10 if x not in [0] else -16), textcoords="offset points",
                    ha="center", fontsize=9.5, fontweight="bold", color=COLOR_TIERED,
                    bbox=dict(boxstyle="round,pad=0.2", facecolor="#D1FAE5", edgecolor="none", alpha=0.9))

    # Explanatory Callouts
    ax.annotate(
        "Peak Contention: 3.4× Latency Explosion\n"
        "(Beeswax instruction stall-hiding cannot prevent\n"
        "macro spinlock contention on htab_lock_bucket)",
        xy=(5, 14.9), xytext=(2.8, 16.8),
        arrowprops=dict(arrowstyle="->", color="#991B1B", lw=1.8),
        fontsize=11.0, fontweight="bold", color="#7F1D1D",
        bbox=dict(boxstyle="round,pad=0.45", facecolor="#FEE2E2", edgecolor="#FCA5A5", alpha=0.95)
    )

    ax.annotate(
        "QoS Tiered: Bounded Tail Latency (~4.1 ms)\n"
        "(Ingress Priority Drops Attacker Traffic Before Map Lock)",
        xy=(2, 4.2), xytext=(1.2, 1.8),
        arrowprops=dict(arrowstyle="->", color="#065F46", lw=1.8),
        fontsize=11.5, fontweight="bold", color="#064E3B",
        bbox=dict(boxstyle="round,pad=0.45", facecolor="#D1FAE5", edgecolor="#6EE7B7", alpha=0.95)
    )

    # Title & Labels
    ax.set_title(
        "Victim Tail Latency (P99) Under Escalating Contention:\n"
        "Sidecarless eBPF (Naive vs Beeswax) vs Sidecar Proxy vs QoS Tiered (Terway)",
        fontweight="bold", pad=16, color="#0F172A"
    )
    ax.set_ylabel("P99 Tail Latency (ms) — Lower is Better", labelpad=12, fontweight="bold")
    ax.set_xlabel("Attacker Flood Intensity (UDP Packets/sec)", labelpad=12, fontweight="bold")

    ax.set_xticks(xi)
    ax.set_xticklabels(FLOOD_LABELS)
    ax.set_ylim(0, 20.0)
    ax.yaxis.set_major_locator(ticker.MultipleLocator(2))
    ax.yaxis.set_minor_locator(ticker.MultipleLocator(1))

    ax.grid(axis="y", linestyle=":", color=COLOR_GRID, linewidth=1.2, zorder=0)
    ax.legend(loc="upper left", frameon=True, framealpha=0.95, edgecolor="#E5E7EB", fancybox=True, fontsize=11.5)

    # Output paths
    out_paths = [
        "/home/udeepa/CLB/test_bed_qos/plots/presentation_sidecarless_beeswax_comparison_p99.png",
        "/home/udeepa/CLB/test_bed/plots/presentation_sidecarless_beeswax_comparison_p99.png"
    ]

    for p in out_paths:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        fig.savefig(p, dpi=300, bbox_inches="tight")
        print(f"[OK] Saved: {p}")

    plt.close(fig)

if __name__ == "__main__":
    main()
