#!/usr/bin/env python3
"""
plot_results.py — Isolation Deficit: Sidecar vs Sidecarless eBPF Mesh.

Reads fortio_load_<FLOOD_ARG>.json from the most-recent result directories.
Load labels match FLOOD_ARR in the bash scripts: (0, u100, u50, u10, u1)
which correspond to hping3 --interval values (~10k, ~20k, ~100k, ~1M pps).

Usage (run from test_bed/):
    python3 plot_results.py
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

# ── Load configuration (matches FLOOD_ARR in bash scripts) ───────────────────
LOAD_KEYS   = ["0", "u100", "u50", "u10", "u1"]
X_LABELS    = ["No load\n(baseline)", "~10k pps\n(u100)", "~20k pps\n(u50)",
               "~100k pps\n(u10)", "~1M pps\n(u1)"]

SC_COLOR = "#2563EB"   # blue  — Sidecar proxy
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
        path = os.path.join(result_dir, f"fortio_load_{key}.json")
        if os.path.exists(path):
            r = parse_fortio(path)
            for k in series:
                series[k].append(r.get(k, np.nan))
        else:
            print(f"  WARNING: {path} not found — inserting NaN", file=sys.stderr)
            for k in series:
                series[k].append(np.nan)
    return series


def bar_panel(ax, sc_vals, sl_vals, title, ylabel="Latency (ms)"):
    width = 0.35
    xi = np.arange(len(X_LABELS))

    b_sc = ax.bar(xi - width / 2, sc_vals, width, label="Sidecar Proxy",
                  color=SC_COLOR, alpha=0.88, zorder=3, edgecolor="white", linewidth=0.8)
    b_sl = ax.bar(xi + width / 2, sl_vals, width, label="Sidecarless eBPF",
                  color=SL_COLOR, alpha=0.88, zorder=3, edgecolor="white", linewidth=0.8)

    for bar in list(b_sc) + list(b_sl):
        h = bar.get_height()
        if np.isfinite(h) and h > 0:
            ax.text(bar.get_x() + bar.get_width() / 2, h + max(h * 0.01, 0.05),
                    f"{h:.1f}", ha="center", va="bottom", fontsize=7, color="#374151",
                    fontweight="bold")

    ax.set_title(title, fontweight="bold", pad=10)
    ax.set_ylabel(ylabel, labelpad=8)
    ax.set_xlabel("Attacker Flood Rate (hping3 --interval)", labelpad=8)
    ax.set_xticks(xi)
    ax.set_xticklabels(X_LABELS, fontsize=8.5)
    ax.yaxis.set_minor_locator(ticker.AutoMinorLocator())
    ax.grid(axis="y", linestyle=":", alpha=0.55, zorder=0)
    ax.set_axisbelow(True)


def line_panel(ax, sc_vals, sl_vals, title, ylabel="Latency (ms)"):
    xi = np.arange(len(X_LABELS))
    ax.plot(xi, sc_vals, marker="s", linestyle="--", linewidth=2.2,
            markersize=8, color=SC_COLOR, label="Sidecar Proxy", zorder=4)
    ax.plot(xi, sl_vals, marker="o", linestyle="-",  linewidth=2.2,
            markersize=8, color=SL_COLOR, label="Sidecarless eBPF", zorder=4)
    ax.fill_between(xi, sc_vals, sl_vals, alpha=0.12, color="#6366F1", zorder=2)

    ax.set_title(title, fontweight="bold", pad=10)
    ax.set_ylabel(ylabel, labelpad=8)
    ax.set_xlabel("Attacker Flood Rate (hping3 --interval)", labelpad=8)
    ax.set_xticks(xi)
    ax.set_xticklabels(X_LABELS, fontsize=8.5)
    ax.yaxis.set_minor_locator(ticker.AutoMinorLocator())
    ax.grid(axis="y", linestyle=":", alpha=0.55, zorder=0)
    ax.set_axisbelow(True)


def main():
    sc_dir = get_latest_dir("sidecar")
    sl_dir = get_latest_dir("sidecarless")

    if not sc_dir or not sl_dir:
        print("ERROR: Missing results directory.", file=sys.stderr)
        sys.exit(1)

    print(f"Reading:\n  Sidecar:     {sc_dir}\n  Sidecarless: {sl_dir}\n")
    sc = load_series(sc_dir)
    sl = load_series(sl_dir)

    # Print table
    print(f"{'Load':<14} {'SC p50':>8} {'SL p50':>8} {'SC p90':>8} {'SL p90':>8} "
          f"{'SC p99':>8} {'SL p99':>8} {'SC QPS':>8} {'SL QPS':>8}")
    print("-" * 78)
    for i, lbl in enumerate(X_LABELS):
        lbl_short = LOAD_KEYS[i]
        print(f"{lbl_short:<14} {sc['p50'][i]:>8.2f} {sl['p50'][i]:>8.2f} "
              f"{sc['p90'][i]:>8.2f} {sl['p90'][i]:>8.2f} "
              f"{sc['p99'][i]:>8.2f} {sl['p99'][i]:>8.2f} "
              f"{sc['qps'][i]:>8.0f} {sl['qps'][i]:>8.0f}")

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
        ("p99",  "Tail (P99) Latency — Isolation Deficit"),
    ]

    for col, (key, title) in enumerate(metrics):
        bar_panel(axes[0][col], sc[key], sl[key], title)
        line_panel(axes[1][col], sc[key], sl[key], title.replace(" — Isolation Deficit", "") + " — Load Trend")

    fig.text(0.005, 0.70, "Bar comparison", va="center", rotation="vertical",
             fontsize=10, color="#6B7280", style="italic")
    fig.text(0.005, 0.28, "Load trend", va="center", rotation="vertical",
             fontsize=10, color="#6B7280", style="italic")

    legend_handles = [
        plt.Line2D([0], [0], color=SC_COLOR, marker="s", linestyle="--",
                   linewidth=2, markersize=9, label="Sidecar Proxy (socat — no eBPF spinlock)"),
        plt.Line2D([0], [0], color=SL_COLOR, marker="o", linestyle="-",
                   linewidth=2, markersize=9, label="Sidecarless eBPF (shared_global_key spinlock)"),
    ]
    fig.legend(handles=legend_handles, loc="upper center",
               bbox_to_anchor=(0.5, 0.955), ncol=2, frameon=True,
               fontsize=11.5, framealpha=0.9, edgecolor="#D1D5DB", fancybox=True)

    fig.suptitle("Isolation Deficit Analysis: Sidecar vs Sidecarless eBPF Mesh",
                 fontsize=16, fontweight="bold", y=0.98, color="#111827")
    fig.text(0.5, 0.932,
             "Victim (fortio 5k QPS) latency under increasing hping3 UDP flood on same veth-vic-br  |  "
             "eBPF: shared_global_key forces single-bucket spinlock contention",
             ha="center", fontsize=9.5, color="#6B7280")

    os.makedirs("results", exist_ok=True)
    out = "results/latency_comparison.png"
    plt.savefig(out, dpi=200, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"\nPlot saved: {os.path.abspath(out)}")


if __name__ == "__main__":
    main()
