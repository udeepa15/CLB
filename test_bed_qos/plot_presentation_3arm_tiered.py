#!/usr/bin/env python3
"""
plot_presentation_3arm_tiered.py — Presentation-Grade Visualization:
Sidecarless vs Sidecar vs QoS Tiered Tail Latency (P99).

Tailored for academic presentations (16:9 widescreen, large high-contrast fonts,
300 DPI, distinct color coding, callout annotations).
"""

import os
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

CLB_DIR = "/home/udeepa/CLB"
CSV_PATH = os.path.join(CLB_DIR, "test_bed_qos", "results", "multi_run_N2_20260817_074312", "results_summary_avg.csv")
OUT_DIR_1 = os.path.join(CLB_DIR, "test_bed_qos", "plots")
OUT_DIR_2 = os.path.join(CLB_DIR, "test_bed", "plots")

os.makedirs(OUT_DIR_1, exist_ok=True)
os.makedirs(OUT_DIR_2, exist_ok=True)

# Academic Presentation Styling
plt.rcParams.update({
    "font.family":       "sans-serif",
    "font.sans-serif":   ["Inter", "DejaVu Sans", "Helvetica", "Arial"],
    "font.size":         13,
    "axes.labelsize":    15,
    "axes.titlesize":    17,
    "xtick.labelsize":   13,
    "ytick.labelsize":   13,
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "axes.linewidth":    1.5,
    "figure.dpi":        300,
})

# Color definitions
COLOR_SIDECARLESS = "#DC2626"  # Crimson Red
COLOR_SIDECAR     = "#2563EB"  # Royal Blue
COLOR_TIERED      = "#059669"  # Emerald Green
COLOR_BG          = "#FAFAFA"  # Off-White
COLOR_GRID        = "#E5E7EB"  # Soft Gridlines

FLOOD_ORDER = ["0", "u500", "u200", "u50", "u20", "u5", "u2"]
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
    if not os.path.exists(CSV_PATH):
        print(f"ERROR: {CSV_PATH} not found.")
        sys.exit(1)

    df = pd.read_csv(CSV_PATH)
    df["p99_ms"] = pd.to_numeric(df["p99_ms"], errors="coerce")

    # Filter to HTTP and target architectures
    df_http = df[(df["protocol"] == "http") & (df["arch"].isin(["sidecarless", "sidecar", "qos_tiered"]))]

    sl_data = df_http[df_http["arch"] == "sidecarless"].set_index("flood_level")["p99_ms"]
    sc_data = df_http[df_http["arch"] == "sidecar"].set_index("flood_level")["p99_ms"]
    ti_data = df_http[df_http["arch"] == "qos_tiered"].set_index("flood_level")["p99_ms"]

    y_sl = [sl_data.get(fl, np.nan) for fl in FLOOD_ORDER]
    y_sc = [sc_data.get(fl, np.nan) for fl in FLOOD_ORDER]
    y_ti = [ti_data.get(fl, np.nan) for fl in FLOOD_ORDER]
    xi = np.arange(len(FLOOD_ORDER))

    # 16:9 canvas
    fig, ax = plt.subplots(figsize=(12, 6.75))
    fig.patch.set_facecolor(COLOR_BG)
    ax.set_facecolor("#FFFFFF")

    # Plot Sidecarless (Crimson Red)
    ax.plot(
        xi, y_sl,
        marker="o", markersize=10, linewidth=3.2,
        color=COLOR_SIDECARLESS, label="Sidecarless eBPF Baseline (No QoS)",
        zorder=5
    )

    # Plot Sidecar (Royal Blue)
    ax.plot(
        xi, y_sc,
        marker="s", markersize=9, linewidth=2.6, linestyle="--",
        color=COLOR_SIDECAR, label="Sidecar Proxy Reference (Socat/Envoy)",
        zorder=4
    )

    # Plot QoS Tiered (Emerald Green)
    ax.plot(
        xi, y_ti,
        marker="^", markersize=10, linewidth=3.0, linestyle="-.",
        color=COLOR_TIERED, label="QoS Tiered (Terway Static eBPF Priority)",
        zorder=6
    )

    # Annotate points with badges
    for x, ys, yc, yt in zip(xi, y_sl, y_sc, y_ti):
        # Sidecarless badge (above line)
        sl_offset_y = 12
        sl_offset_x = -10 if x == 0 else 0
        ax.annotate(
            f"{ys:.1f} ms",
            xy=(x, ys), xytext=(sl_offset_x, sl_offset_y), textcoords="offset points",
            ha="center", va="bottom",
            fontsize=10.5, fontweight="bold", color=COLOR_SIDECARLESS,
            bbox=dict(boxstyle="round,pad=0.2", facecolor="#FEE2E2", edgecolor="none", alpha=0.9)
        )

        # Sidecar badge (below line)
        sc_offset_y = -18
        sc_offset_x = -14 if x in [1, 4, 5, 6] else 0
        ax.annotate(
            f"{yc:.1f} ms",
            xy=(x, yc), xytext=(sc_offset_x, sc_offset_y), textcoords="offset points",
            ha="center", va="top",
            fontsize=10.5, fontweight="bold", color=COLOR_SIDECAR,
            bbox=dict(boxstyle="round,pad=0.2", facecolor="#DBEAFE", edgecolor="none", alpha=0.9)
        )

        # QoS Tiered badge
        ti_offset_y = 11 if x in [1, 4, 5, 6] else -18
        ti_offset_x = 16 if x in [1, 4, 5, 6] else (14 if x == 0 else 0)
        ti_va = "bottom" if x in [1, 4, 5, 6] else "top"
        ax.annotate(
            f"{yt:.1f} ms",
            xy=(x, yt), xytext=(ti_offset_x, ti_offset_y), textcoords="offset points",
            ha="center", va=ti_va,
            fontsize=10.5, fontweight="bold", color=COLOR_TIERED,
            bbox=dict(boxstyle="round,pad=0.2", facecolor="#D1FAE5", edgecolor="none", alpha=0.9)
        )

    # Highlight Callout 1: Peak Contention Explosion
    peak_x = 5  # index of u5 (200k pps)
    peak_y = y_sl[peak_x]
    ax.annotate(
        "Peak Contention:\n3.4× Latency Explosion\n(Shared Map Spinlock Contention)",
        xy=(peak_x, peak_y), xytext=(peak_x - 2.1, peak_y + 1.8),
        arrowprops=dict(facecolor="#991B1B", edgecolor="#991B1B", arrowstyle="->", lw=2.0),
        fontsize=11.5, fontweight="bold", color="#991B1B",
        bbox=dict(boxstyle="round,pad=0.45", facecolor="#FEF2F2", edgecolor="#F87171", lw=1.5),
        zorder=7
    )

    # Highlight Callout 2: QoS Tiered Stability
    tier_x = 2  # index of u200 (5k pps)
    tier_y = y_ti[tier_x]
    ax.annotate(
        "QoS Tiered: Bounded Tail Latency (~4.1 ms)\n(Ingress Priority Drops Attacker Traffic Before Map Lock)",
        xy=(tier_x, tier_y), xytext=(tier_x - 0.2, tier_y - 2.4),
        arrowprops=dict(facecolor="#065F46", edgecolor="#065F46", arrowstyle="->", lw=1.8),
        fontsize=11, fontweight="bold", color="#065F46",
        bbox=dict(boxstyle="round,pad=0.45", facecolor="#ECFDF5", edgecolor="#34D399", lw=1.5),
        zorder=7
    )

    # Titles & Labels
    ax.set_title(
        "Victim Tail Latency (P99) Under Escalating Contention:\nSidecarless eBPF vs Sidecar Proxy vs QoS Tiered (Terway)",
        fontweight="bold", fontsize=16.5, pad=18, color="#0F172A"
    )
    ax.set_ylabel("P99 Tail Latency (ms) — Lower is Better", labelpad=12, fontweight="bold")
    ax.set_xlabel("Attacker Flood Intensity (UDP Packets/sec)", labelpad=12, fontweight="bold")

    ax.set_xticks(xi)
    ax.set_xticklabels(FLOOD_LABELS, fontsize=12)
    ax.set_ylim(0.0, 19.5)
    ax.yaxis.set_major_locator(ticker.MultipleLocator(2.0))
    ax.yaxis.set_minor_locator(ticker.MultipleLocator(1.0))
    ax.grid(axis="y", linestyle=":", color=COLOR_GRID, linewidth=1.2, zorder=0)

    # Legend
    ax.legend(
        loc="upper left", frameon=True, facecolor="#FFFFFF",
        edgecolor="#D1D5DB", fontsize=12, framealpha=0.95
    )

    plt.tight_layout()

    out_file1 = os.path.join(OUT_DIR_1, "presentation_sidecar_sidecarless_tiered_p99.png")
    out_file2 = os.path.join(OUT_DIR_2, "presentation_sidecar_sidecarless_tiered_p99.png")

    fig.savefig(out_file1, dpi=300, bbox_inches="tight", facecolor=fig.get_facecolor())
    fig.savefig(out_file2, dpi=300, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)

    print(f"[OK] Generated 3-Architecture Presentation Plot:")
    print(f"  1. {out_file1}")
    print(f"  2. {out_file2}")


if __name__ == "__main__":
    main()
