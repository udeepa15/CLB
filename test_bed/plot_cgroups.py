#!/usr/bin/env python3
"""
plot_cgroups.py — Cgroups Quota Sweep: Sidecarless eBPF Mesh.

Reads fortio_vic2_limit_<LIMIT>.json from the most-recent result directories.

Usage (run from test_bed/):
    python3 plot_cgroups.py
"""

import json
import glob
import os
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

# ── Style ─────────────────────────────────────────────────────────────────────
try:
    plt.style.use("seaborn-v0_8-whitegrid")
except OSError:
    try:
        plt.style.use("seaborn-whitegrid")
    except OSError:
        pass

plt.rcParams.update({
    "font.family":       "DejaVu Sans",
    "font.size":         11,
    "axes.labelsize":    12,
    "axes.titlesize":    13,
    "xtick.labelsize":   10,
    "ytick.labelsize":   10,
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "axes.linewidth":    1.2,
})

# ── Load configuration ────────────────────────────────────────────────────────
LOAD_KEYS   = ["80", "85", "90", "95", "99"]
X_LABELS    = ["80%", "85%", "90%", "95%", "99%"]

SL_COLOR = "#F97316"   # amber — Sidecarless eBPF


def get_latest_dir(arch: str):
    dirs = sorted(glob.glob(f"results/{arch}/*/"))
    return dirs[-1].rstrip("/") if dirs else None


def parse_fortio(filepath: str) -> dict:
    with open(filepath) as f:
        data = json.load(f)
    result = {
        "qps":   data.get("ActualQPS", 0),
        "count": data["DurationHistogram"].get("Count", 0),
    }
    for p in data["DurationHistogram"].get("Percentiles", []):
        pct = p["Percentile"]
        val = p["Value"] * 1000.0      # s -> ms
        if   pct == 50:   result["p50"]  = val
        elif pct == 90:   result["p90"]  = val
        elif pct == 99:   result["p99"]  = val
        elif pct == 99.9: result["p999"] = val
    return result


def load_series(result_dir: str) -> dict:
    series = {k: [] for k in ("p50", "p90", "p99", "p999", "qps")}
    for key in LOAD_KEYS:
        path = os.path.join(result_dir, f"fortio_vic2_limit_{key}.json")
        if os.path.exists(path):
            r = parse_fortio(path)
            for k in series:
                series[k].append(r.get(k, np.nan))
        else:
            print(f"  WARNING: {path} not found — inserting NaN", file=sys.stderr)
            for k in series:
                series[k].append(np.nan)
    return series


def bar_panel(ax, sl_vals, title, ylabel="Latency (ms)"):
    width = 0.5
    xi = np.arange(len(X_LABELS))

    b_sl = ax.bar(xi, sl_vals, width, label="Sidecarless eBPF (u20 flood)",
                  color=SL_COLOR, alpha=0.88, zorder=3, edgecolor="white", linewidth=0.8)

    for bar in b_sl:
        h = bar.get_height()
        if np.isfinite(h) and h > 0:
            ax.text(bar.get_x() + bar.get_width() / 2, h + max(h * 0.01, 0.05),
                    f"{h:.1f}", ha="center", va="bottom", fontsize=9, color="#374151",
                    fontweight="bold")

    ax.set_title(title, fontweight="bold", pad=10)
    ax.set_ylabel(ylabel, labelpad=8)
    ax.set_xlabel("Container CPU Limit (Quota %)", labelpad=8)
    ax.set_xticks(xi)
    ax.set_xticklabels(X_LABELS, fontsize=10, ha="center")
    ax.yaxis.set_minor_locator(ticker.AutoMinorLocator())
    ax.grid(axis="y", linestyle=":", alpha=0.55, zorder=0)
    ax.set_axisbelow(True)


def line_panel(ax, sl_vals, title, ylabel="Latency (ms)"):
    xi = np.arange(len(X_LABELS))
    ax.plot(xi, sl_vals, marker="o", linestyle="-",  linewidth=2.2,
            markersize=8, color=SL_COLOR, label="Sidecarless eBPF (u20 flood)", zorder=4)
    ax.fill_between(xi, np.zeros_like(sl_vals), sl_vals, alpha=0.12, color=SL_COLOR, zorder=2)

    ax.set_title(title, fontweight="bold", pad=10)
    ax.set_ylabel(ylabel, labelpad=8)
    ax.set_xlabel("Container CPU Limit (Quota %)", labelpad=8)
    ax.set_xticks(xi)
    ax.set_xticklabels(X_LABELS, fontsize=10, ha="center")
    ax.yaxis.set_minor_locator(ticker.AutoMinorLocator())
    ax.grid(axis="y", linestyle=":", alpha=0.55, zorder=0)
    ax.set_axisbelow(True)


def main():
    sl_dir = get_latest_dir("cgroups")

    if not sl_dir:
        print("Error: No results/cgroups directory found.")
        sys.exit(1)

    print(f"Reading:\n  Cgroups: {sl_dir}\n")
    sl = load_series(sl_dir)

    # Print table
    print(f"{'Limit (%)':<14} {'SL p50':>8} {'SL p90':>8} "
          f"{'SL p99':>8} {'SL QPS':>8}")
    print("-" * 52)
    for i, lbl in enumerate(X_LABELS):
        lbl_short = LOAD_KEYS[i]
        print(f"{lbl_short:<14} {sl['p50'][i]:>8.2f} "
              f"{sl['p90'][i]:>8.2f} "
              f"{sl['p99'][i]:>8.2f} "
              f"{sl['qps'][i]:>8.0f}")

    # ── Figure ────────────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(18, 11))
    fig.patch.set_facecolor("#F8FAFC")
    gs = fig.add_gridspec(2, 3, hspace=0.48, wspace=0.38,
                          left=0.06, right=0.97, top=0.87, bottom=0.10)

    axes = [[fig.add_subplot(gs[r, c]) for c in range(3)] for r in range(2)]
    for row in axes:
        for ax in row:
            ax.set_facecolor("#FFFFFF")

    metrics = [
        ("p50",  "Median (P50) Latency"),
        ("p90",  "P90 Latency"),
        ("p99",  "Tail (P99) Latency — Lock Contention"),
    ]

    for col, (key, title) in enumerate(metrics):
        bar_panel(axes[0][col], sl[key], title)
        line_panel(axes[1][col], sl[key], title.replace(" — Lock Contention", "") + " — CPU Quota Trend")

    fig.text(0.005, 0.70, "Bar comparison", va="center", rotation="vertical",
             fontsize=10, color="#6B7280", style="italic")
    fig.text(0.005, 0.28, "Load trend", va="center", rotation="vertical",
             fontsize=10, color="#6B7280", style="italic")

    legend_handles = [
        plt.Line2D([0], [0], color=SL_COLOR, marker="o", linestyle="-",
                   linewidth=2, markersize=9, label="Sidecarless eBPF (fixed at u20 flood)"),
    ]
    fig.legend(handles=legend_handles, loc="upper center",
               bbox_to_anchor=(0.5, 0.955), ncol=1, frameon=True,
               fontsize=11.5, framealpha=0.9, edgecolor="#D1D5DB", fancybox=True)

    fig.suptitle("Impact of Cgroup CPU Limits on eBPF Lock Contention",
                 fontsize=16, fontweight="bold", y=0.98, color="#111827")
    fig.text(0.5, 0.932,
             "Victim (fortio 50 QPS) latency under peak contention (u20 flood) across varying CPU Quotas",
             ha="center", fontsize=9.5, color="#6B7280")

    os.makedirs("results", exist_ok=True)
    out = "results/cgroups_comparison.png"
    plt.savefig(out, dpi=200, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"\nPlot saved: {os.path.abspath(out)}")


if __name__ == "__main__":
    main()
