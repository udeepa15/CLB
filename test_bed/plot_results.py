#!/usr/bin/env python3
"""
plot_results.py — Latency comparison: Sidecar Proxy vs Sidecarless eBPF Mesh.

Reads fortio_load_<LOAD>.json from the most-recent sidecar / sidecarless
results directories and produces a multi-panel latency comparison figure.

Usage (run from test_bed/):
    python3 plot_results.py
"""

import json
import glob
import os
import sys
import matplotlib
matplotlib.use("Agg")          # headless / WSL safe
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

# ── Typography & style ──────────────────────────────────────────────────────
try:
    plt.style.use("seaborn-v0_8-whitegrid")
except OSError:
    plt.style.use("seaborn-whitegrid" if "seaborn-whitegrid" in plt.style.available else "default")

FONT_FAMILY = "DejaVu Sans"
plt.rcParams.update({
    "font.family":        FONT_FAMILY,
    "font.size":          11,
    "axes.labelsize":     12,
    "axes.titlesize":     13,
    "xtick.labelsize":    10,
    "ytick.labelsize":    10,
    "figure.titlesize":   16,
    "axes.spines.top":    False,
    "axes.spines.right":  False,
    "axes.linewidth":     1.2,
})

# ── Load levels (must match LOAD_ARR in the bash scripts) ───────────────────
LOAD_LABELS = ["0", "1G", "2G", "4G", "8G"]
X_LABELS    = ["No load", "1 Gb/s", "2 Gb/s", "4 Gb/s", "8 Gb/s"]

# ── Colour palette ────────────────────────────────────────────────────────────
SC_COLOR   = "#2563EB"   # vivid blue  — Sidecar proxy
SL_COLOR   = "#F97316"   # vivid amber — Sidecarless eBPF


def get_latest_dir(arch: str) -> str | None:
    dirs = sorted(glob.glob(f"results/{arch}/*/"))
    return dirs[-1].rstrip("/") if dirs else None


def parse_fortio(filepath: str) -> dict:
    """Return dict with p50/p90/p99/p999 latencies in milliseconds."""
    with open(filepath) as f:
        data = json.load(f)
    result = {"qps": data.get("ActualQPS", 0), "count": data["DurationHistogram"].get("Count", 0)}
    for p in data["DurationHistogram"].get("Percentiles", []):
        pct = p["Percentile"]
        val = p["Value"] * 1000.0          # seconds → milliseconds
        if   pct == 50:   result["p50"]  = val
        elif pct == 90:   result["p90"]  = val
        elif pct == 99:   result["p99"]  = val
        elif pct == 99.9: result["p999"] = val
    return result


def load_series(result_dir: str) -> dict:
    """Load all load-level fortio files for one architecture."""
    series = {k: [] for k in ("p50", "p90", "p99", "p999", "qps", "count")}
    for load in LOAD_LABELS:
        path = os.path.join(result_dir, f"fortio_load_{load}.json")
        if os.path.exists(path):
            r = parse_fortio(path)
            for k in series:
                series[k].append(r.get(k, np.nan))
        else:
            print(f"  WARNING: {path} not found — inserting NaN", file=sys.stderr)
            for k in series:
                series[k].append(np.nan)
    return series


def make_bar_group(ax, x, sc_vals, sl_vals, title, ylabel="Latency (ms)"):
    """Render a grouped bar chart for one latency metric."""
    width = 0.35
    xi    = np.arange(len(x))

    bars_sc = ax.bar(xi - width / 2, sc_vals,  width, label="Sidecar Proxy",
                     color=SC_COLOR, alpha=0.88, zorder=3,
                     edgecolor="white", linewidth=0.8)
    bars_sl = ax.bar(xi + width / 2, sl_vals, width, label="Sidecarless eBPF",
                     color=SL_COLOR, alpha=0.88, zorder=3,
                     edgecolor="white", linewidth=0.8)

    # Value labels on top of bars
    for bar in list(bars_sc) + list(bars_sl):
        h = bar.get_height()
        if np.isfinite(h):
            ax.text(bar.get_x() + bar.get_width() / 2, h + 0.05,
                    f"{h:.2f}", ha="center", va="bottom", fontsize=7.5,
                    color="#374151", fontweight="bold")

    ax.set_title(title, fontweight="bold", pad=10)
    ax.set_ylabel(ylabel, labelpad=8)
    ax.set_xlabel("Attacker Load (iperf3 UDP bandwidth)", labelpad=8)
    ax.set_xticks(xi)
    ax.set_xticklabels(x, fontsize=9.5)
    ax.yaxis.set_minor_locator(ticker.AutoMinorLocator())
    ax.grid(axis="y", linestyle=":", alpha=0.55, zorder=0)
    ax.set_axisbelow(True)
    return bars_sc, bars_sl


def make_line_panel(ax, x, sc_vals, sl_vals, title, ylabel="Latency (ms)"):
    """Render a line chart comparing both architectures over load levels."""
    xi = np.arange(len(x))
    ax.plot(xi, sc_vals, marker="s", linestyle="--", linewidth=2.2,
            markersize=8, color=SC_COLOR, label="Sidecar Proxy", zorder=4)
    ax.plot(xi, sl_vals, marker="o", linestyle="-",  linewidth=2.2,
            markersize=8, color=SL_COLOR, label="Sidecarless eBPF", zorder=4)

    # Shaded area between curves
    ax.fill_between(xi, sc_vals, sl_vals, alpha=0.12, color="#6366F1", zorder=2,
                    label="_nolegend_")

    ax.set_title(title, fontweight="bold", pad=10)
    ax.set_ylabel(ylabel, labelpad=8)
    ax.set_xlabel("Attacker Load (iperf3 UDP bandwidth)", labelpad=8)
    ax.set_xticks(xi)
    ax.set_xticklabels(x, fontsize=9.5)
    ax.yaxis.set_minor_locator(ticker.AutoMinorLocator())
    ax.grid(axis="y", linestyle=":", alpha=0.55, zorder=0)
    ax.set_axisbelow(True)


def main():
    sidecar_dir    = get_latest_dir("sidecar")
    sidecarless_dir = get_latest_dir("sidecarless")

    if not sidecar_dir or not sidecarless_dir:
        print("ERROR: Missing results directory for sidecar or sidecarless.", file=sys.stderr)
        sys.exit(1)

    print(f"Reading results from:")
    print(f"  Sidecar:     {sidecar_dir}")
    print(f"  Sidecarless: {sidecarless_dir}")

    sc = load_series(sidecar_dir)
    sl = load_series(sidecarless_dir)

    # ── Print data table ──────────────────────────────────────────────────────
    print(f"\n{'Load':<6} {'SC p50':>8} {'SL p50':>8} {'SC p90':>8} {'SL p90':>8} {'SC p99':>8} {'SL p99':>8}")
    print("-" * 56)
    for i, lbl in enumerate(X_LABELS):
        print(f"{lbl:<6} {sc['p50'][i]:>8.3f} {sl['p50'][i]:>8.3f} "
              f"{sc['p90'][i]:>8.3f} {sl['p90'][i]:>8.3f} "
              f"{sc['p99'][i]:>8.3f} {sl['p99'][i]:>8.3f}")

    # ── Figure layout: 2 rows × 3 cols ───────────────────────────────────────
    fig = plt.figure(figsize=(18, 11))
    fig.patch.set_facecolor("#F8FAFC")

    gs = fig.add_gridspec(2, 3, hspace=0.42, wspace=0.35,
                          left=0.06, right=0.97, top=0.88, bottom=0.10)

    ax_p50_bar  = fig.add_subplot(gs[0, 0])
    ax_p90_bar  = fig.add_subplot(gs[0, 1])
    ax_p99_bar  = fig.add_subplot(gs[0, 2])
    ax_p50_line = fig.add_subplot(gs[1, 0])
    ax_p90_line = fig.add_subplot(gs[1, 1])
    ax_p99_line = fig.add_subplot(gs[1, 2])

    for ax in (ax_p50_bar, ax_p90_bar, ax_p99_bar,
               ax_p50_line, ax_p90_line, ax_p99_line):
        ax.set_facecolor("#FFFFFF")

    # Row 1 — bar charts
    bs_sc, bs_sl = make_bar_group(ax_p50_bar, X_LABELS, sc["p50"], sl["p50"],
                                  "Median (P50) Latency")
    make_bar_group(ax_p90_bar, X_LABELS, sc["p90"], sl["p90"],
                   "P90 Latency")
    make_bar_group(ax_p99_bar, X_LABELS, sc["p99"], sl["p99"],
                   "Tail (P99) Latency")

    # Row 2 — line charts
    make_line_panel(ax_p50_line, X_LABELS, sc["p50"], sl["p50"],
                    "Median (P50) — Load Trend")
    make_line_panel(ax_p90_line, X_LABELS, sc["p90"], sl["p90"],
                    "P90 — Load Trend")
    make_line_panel(ax_p99_line, X_LABELS, sc["p99"], sl["p99"],
                    "Tail (P99) — Load Trend")

    # ── Row labels ────────────────────────────────────────────────────────────
    fig.text(0.005, 0.70, "Bar comparison", va="center", rotation="vertical",
             fontsize=10, color="#6B7280", style="italic")
    fig.text(0.005, 0.30, "Load trend", va="center", rotation="vertical",
             fontsize=10, color="#6B7280", style="italic")

    # ── Shared legend ─────────────────────────────────────────────────────────
    legend_handles = [
        plt.Line2D([0], [0], color=SC_COLOR, marker="s", linestyle="--",
                   linewidth=2, markersize=9, label="Sidecar Proxy (socat baseline)"),
        plt.Line2D([0], [0], color=SL_COLOR, marker="o", linestyle="-",
                   linewidth=2, markersize=9, label="Sidecarless eBPF (shared-key contention)"),
    ]
    fig.legend(handles=legend_handles, loc="upper center",
               bbox_to_anchor=(0.5, 0.955), ncol=2, frameon=True,
               fontsize=12, framealpha=0.9, edgecolor="#D1D5DB",
               fancybox=True)

    # ── Main title & subtitle ─────────────────────────────────────────────────
    fig.suptitle("Isolation Deficit Analysis: Sidecar vs Sidecarless eBPF Mesh",
                 fontsize=16, fontweight="bold", y=0.98, color="#111827")
    fig.text(0.5, 0.935,
             "Victim (fortio) latency under increasing iperf3 UDP attacker flood  |  "
             "eBPF map: shared key (single-bucket spinlock contention)",
             ha="center", fontsize=10, color="#6B7280")

    # ── Save ──────────────────────────────────────────────────────────────────
    os.makedirs("results", exist_ok=True)
    output_png = "results/latency_comparison.png"
    plt.savefig(output_png, dpi=200, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"\nPlot saved to: {os.path.abspath(output_png)}")


if __name__ == "__main__":
    main()
