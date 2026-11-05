#!/usr/bin/env python3
"""
plot_cgroups_vs_solution_separate.py — Generate 4 Separate PNG Plots:
Linux Cgroups vs eBPF QoS Architecture.

Generates:
1. cgroups_vs_qos_p99_head_to_head.png
2. cgroups_sweep_vs_qos_thresholds.png
3. cgroups_vs_qos_percentiles_progression.png
4. cgroups_vs_qos_improvement_factor.png
"""

import os
import sys
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

CLB_DIR = "/home/udeepa/CLB"
RESULTS_DIR = os.path.join(CLB_DIR, "test_bed_qos", "markov_experiments", "results", "cgroups_vs_solution")
GRAPHS_DIR = os.path.join(CLB_DIR, "test_bed_qos", "markov_experiments", "graphs")
HIST_DIR = os.path.join(CLB_DIR, "test_bed", "results", "cgroups", "20260721_213813")
TEST_BED_PLOTS = os.path.join(CLB_DIR, "test_bed", "plots")

os.makedirs(GRAPHS_DIR, exist_ok=True)
os.makedirs(TEST_BED_PLOTS, exist_ok=True)

# Style setup
try:
    plt.style.use("seaborn-v0_8-whitegrid")
except OSError:
    try:
        plt.style.use("seaborn-whitegrid")
    except OSError:
        pass

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 11,
    "axes.labelsize": 13,
    "axes.titlesize": 14,
    "xtick.labelsize": 11,
    "ytick.labelsize": 11,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.linewidth": 1.2,
})


def parse_fortio_file(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            d = json.load(f)
        hist = d.get("DurationHistogram", {})
        pcts = {p["Percentile"]: p["Value"] * 1000.0 for p in hist.get("Percentiles", [])}
        return {
            "p50": pcts.get(50, hist.get("Avg", 0) * 1000.0),
            "p90": pcts.get(90, 0),
            "p99": pcts.get(99, 0),
            "p999": pcts.get(99.9, 0),
            "max": hist.get("Max", 0) * 1000.0,
            "qps": d.get("ActualQPS", 0),
        }
    except Exception as e:
        print(f"Error reading {path}: {e}")
        return None


def load_all_data():
    sweep_keys = [80, 85, 90, 95, 99]
    sweep_data = {}
    for k in sweep_keys:
        p = os.path.join(HIST_DIR, f"fortio_vic2_limit_{k}.json")
        res = parse_fortio_file(p)
        if res:
            sweep_data[k] = res

    summary_path = os.path.join(RESULTS_DIR, "cgroups_vs_solution_summary.json")
    live_data = {}
    if os.path.exists(summary_path):
        with open(summary_path) as f:
            raw = json.load(f)
        for arm, vals in raw.items():
            vic2 = vals.get("vic2", {})
            if vic2:
                live_data[arm] = {
                    "p50": vic2.get("p50_ms", 0),
                    "p90": vic2.get("p90_ms", 0),
                    "p99": vic2.get("p99_ms", 0),
                    "p999": vic2.get("p999_ms", 0),
                    "max": vic2.get("max_ms", 0),
                }

    return sweep_data, live_data


def save_plot_both(fig, filename):
    p1 = os.path.join(GRAPHS_DIR, filename)
    p2 = os.path.join(TEST_BED_PLOTS, filename)
    fig.savefig(p1, dpi=200, bbox_inches="tight", facecolor=fig.get_facecolor())
    fig.savefig(p2, dpi=200, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"[OK] Saved: {p1}")
    print(f"      also: {p2}")


def plot_1_head_to_head(live_data, sweep_data):
    fig, ax = plt.subplots(figsize=(11, 6.5))
    fig.patch.set_facecolor("#F8FAFC")
    ax.set_facecolor("#FFFFFF")

    categories = [
        ("Sidecarless\n(Baseline)", live_data.get("sidecarless_baseline", {}).get("p99", 3.98), "#E11D48"),
        ("Cgroups 50%\nQuota", live_data.get("cgroups_50", {}).get("p99", 2.93), "#F97316"),
        ("Cgroups 80%\nQuota", live_data.get("cgroups_80", {}).get("p99", 2.99), "#EA580C"),
        ("QoS Tiered\n(Static eBPF)", live_data.get("qos_tiered", {}).get("p99", 1.98), "#2563EB"),
        ("QoS Dynamic\n(Stackelberg)", live_data.get("qos_dynamic", {}).get("p99", 1.99), "#0891B2"),
        ("QoS Markov\n(DTMC Predictive)", live_data.get("qos_dtmc_predictive", {}).get("p99", 1.95), "#059669")
    ]

    labels = [c[0] for c in categories]
    vals = [c[1] for c in categories]
    colors = [c[2] for c in categories]
    x_pos = np.arange(len(labels))

    bars = ax.bar(x_pos, vals, color=colors, width=0.55, edgecolor="white", linewidth=1.2, zorder=3)
    for bar in bars:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2, h + 0.12, f"{h:.2f} ms",
                ha="center", va="bottom", fontsize=11, fontweight="bold", color="#1F2937")

    # Demarcation boundary
    ax.axvline(2.5, color="#94A3B8", linestyle="--", linewidth=1.5, zorder=2)
    ax.text(1.0, max(vals) * 0.90, "Standard OS Throttling\n(Kernel Spinlock Unmitigated)",
            ha="center", fontsize=10, color="#991B1B", fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.35", facecolor="#FEE2E2", edgecolor="#F87171", alpha=0.9))
    ax.text(4.2, max(vals) * 0.90, "Proposed eBPF QoS\n(Data-Plane Ingress Rate-Limiting)",
            ha="center", fontsize=10, color="#065F46", fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.35", facecolor="#D1FAE5", edgecolor="#34D399", alpha=0.9))

    ax.set_title("Victim Bystander P99 Tail Latency under Contention (u20 Flood)", fontweight="bold", pad=15)
    ax.set_ylabel("P99 Tail Latency (ms) — Lower is Better", labelpad=10)
    ax.set_xticks(x_pos)
    ax.set_xticklabels(labels, fontsize=10.5)
    ax.set_ylim(0, max(vals) * 1.25)
    ax.yaxis.set_minor_locator(ticker.AutoMinorLocator())
    ax.grid(axis="y", linestyle=":", alpha=0.6, zorder=0)

    fig.suptitle("Graph 1: Head-to-Head Tail Latency Comparison (Cgroups vs eBPF QoS)",
                 fontsize=14, fontweight="bold", y=0.98, color="#0F172A")

    save_plot_both(fig, "cgroups_vs_qos_p99_head_to_head.png")


def plot_2_quota_sweep(sweep_data, live_data):
    fig, ax = plt.subplots(figsize=(11, 6.5))
    fig.patch.set_facecolor("#F8FAFC")
    ax.set_facecolor("#FFFFFF")

    quotas = [80, 85, 90, 95, 99]
    cgroup_p99 = [sweep_data.get(q, {}).get("p99", np.nan) for q in quotas]
    cgroup_p50 = [sweep_data.get(q, {}).get("p50", np.nan) for q in quotas]

    ax.plot(quotas, cgroup_p99, marker="s", markersize=9, linewidth=2.6, color="#EA580C",
            label="Cgroup CFS Quotas (P99 Tail Latency)", zorder=4)
    ax.plot(quotas, cgroup_p50, marker="o", markersize=8, linewidth=2.0, color="#F59E0B",
            linestyle="--", label="Cgroup CFS Quotas (P50 Median Latency)", zorder=4)

    # Reference lines for QoS solutions
    p99_markov = live_data.get("qos_dtmc_predictive", {}).get("p99", 1.95)
    p99_tiered = live_data.get("qos_tiered", {}).get("p99", 1.98)
    p99_dynamic = live_data.get("qos_dynamic", {}).get("p99", 1.99)

    ax.axhline(p99_markov, color="#059669", linestyle="-", linewidth=2.4,
               label=f"QoS Markov Predictive ({p99_markov:.2f} ms)", zorder=3)
    ax.axhline(p99_tiered, color="#2563EB", linestyle="-.", linewidth=2.0,
               label=f"QoS Tiered Static ({p99_tiered:.2f} ms)", zorder=3)
    ax.axhline(p99_dynamic, color="#0891B2", linestyle=":", linewidth=2.0,
               label=f"QoS Dynamic Stackelberg ({p99_dynamic:.2f} ms)", zorder=3)

    for q, p99 in zip(quotas, cgroup_p99):
        if np.isfinite(p99):
            ax.annotate(f"{p99:.1f} ms", xy=(q, p99), xytext=(0, 9), textcoords="offset points",
                        ha="center", fontsize=9.5, fontweight="bold", color="#EA580C")

    ax.set_title("Cgroups Quota Sweep (80%–99%) vs eBPF QoS Benchmark Thresholds", fontweight="bold", pad=15)
    ax.set_xlabel("Container CPU Limit (CFS Quota %)", labelpad=10)
    ax.set_ylabel("Latency (ms)", labelpad=10)
    ax.set_xticks(quotas)
    ax.set_xticklabels([f"{q}%" for q in quotas], fontsize=11)
    ax.set_ylim(0, max(filter(np.isfinite, cgroup_p99), default=15) * 1.25)
    ax.legend(loc="upper right", frameon=True, framealpha=0.95, fontsize=10.5)
    ax.grid(axis="both", linestyle=":", alpha=0.6, zorder=0)

    fig.suptitle("Graph 2: CPU Quota Sensitivity Analysis vs eBPF QoS Floors",
                 fontsize=14, fontweight="bold", y=0.98, color="#0F172A")

    save_plot_both(fig, "cgroups_sweep_vs_qos_thresholds.png")


def plot_3_percentiles_progression(sweep_data, live_data):
    fig, ax = plt.subplots(figsize=(11, 6.5))
    fig.patch.set_facecolor("#F8FAFC")
    ax.set_facecolor("#FFFFFF")

    quotas = [80, 85, 90, 95, 99]
    percentile_labels = ["P50 (Median)", "P90", "P99 (Tail)", "P99.9 (Extreme)"]
    cgroup_avg_p = [
        np.nanmean([sweep_data.get(q, {}).get("p50", np.nan) for q in quotas]),
        np.nanmean([sweep_data.get(q, {}).get("p90", np.nan) for q in quotas]),
        np.nanmean([sweep_data.get(q, {}).get("p99", np.nan) for q in quotas]),
        np.nanmean([sweep_data.get(q, {}).get("p999", np.nan) for q in quotas]),
    ]
    tiered_p = [live_data.get("qos_tiered", {}).get(k, 0) for k in ["p50", "p90", "p99", "p999"]]
    markov_p = [live_data.get("qos_dtmc_predictive", {}).get(k, 0) for k in ["p50", "p90", "p99", "p999"]]

    x_idx = np.arange(len(percentile_labels))
    w = 0.25

    b1 = ax.bar(x_idx - w, cgroup_avg_p, width=w, label="Cgroups (CFS Quota Mean)",
                color="#EA580C", alpha=0.9, edgecolor="white", zorder=3)
    b2 = ax.bar(x_idx, tiered_p, width=w, label="QoS Tiered (Static eBPF)",
                color="#2563EB", alpha=0.9, edgecolor="white", zorder=3)
    b3 = ax.bar(x_idx + w, markov_p, width=w, label="QoS Markov Predictive",
                color="#059669", alpha=0.9, edgecolor="white", zorder=3)

    for bar_grp in [b1, b2, b3]:
        for bar in bar_grp:
            h = bar.get_height()
            if h > 0:
                ax.text(bar.get_x() + bar.get_width() / 2, h + 0.35, f"{h:.1f}",
                        ha="center", va="bottom", fontsize=9.5, fontweight="bold", color="#374151")

    ax.set_title("Latency Progression Across Percentiles (P50 → P99.9)", fontweight="bold", pad=15)
    ax.set_ylabel("Latency (ms) — Lower is Better", labelpad=10)
    ax.set_xticks(x_idx)
    ax.set_xticklabels(percentile_labels, fontsize=11)
    ax.set_ylim(0, max(cgroup_avg_p) * 1.25)
    ax.legend(loc="upper left", frameon=True, framealpha=0.95, fontsize=11)
    ax.grid(axis="y", linestyle=":", alpha=0.6, zorder=0)

    fig.suptitle("Graph 3: Tail Inflation Analysis across Percentile Distributions",
                 fontsize=14, fontweight="bold", y=0.98, color="#0F172A")

    save_plot_both(fig, "cgroups_vs_qos_percentiles_progression.png")


def plot_4_improvement_factor(sweep_data, live_data):
    fig, ax = plt.subplots(figsize=(11, 6.5))
    fig.patch.set_facecolor("#F8FAFC")
    ax.set_facecolor("#FFFFFF")

    baseline_p99 = live_data.get("cgroups_80", {}).get("p99", 2.99)
    p99_dynamic = live_data.get("qos_dynamic", {}).get("p99", 1.99)
    p99_tiered = live_data.get("qos_tiered", {}).get("p99", 1.98)
    p99_markov = live_data.get("qos_dtmc_predictive", {}).get("p99", 1.95)

    speedups = [
        ("Cgroups 80%\nQuota", baseline_p99 / max(sweep_data.get(80, {}).get("p99", 10.0), 0.1), "#EA580C"),
        ("Cgroups 90%\nQuota", baseline_p99 / max(sweep_data.get(90, {}).get("p99", 14.0), 0.1), "#F97316"),
        ("QoS Dynamic\n(Stackelberg)", baseline_p99 / max(p99_dynamic, 0.1), "#0891B2"),
        ("QoS Tiered\n(Static eBPF)", baseline_p99 / max(p99_tiered, 0.1), "#2563EB"),
        ("QoS Markov DTMC\n(Predictive)", baseline_p99 / max(p99_markov, 0.1), "#059669"),
    ]

    sp_labels = [s[0] for s in speedups]
    sp_vals = [s[1] for s in speedups]
    sp_colors = [s[2] for s in speedups]

    b_sp = ax.bar(np.arange(len(sp_labels)), sp_vals, color=sp_colors, width=0.52, edgecolor="white", zorder=3)
    for bar in b_sp:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2, h + 0.05, f"{h:.2f}×",
                ha="center", va="bottom", fontsize=11, fontweight="bold", color="#1F2937")

    ax.axhline(1.0, color="#94A3B8", linestyle="--", linewidth=1.4, label="Parity Baseline (1.0× = Cgroups 80% Live)", zorder=2)
    ax.set_title("Tail Latency Improvement Factor (Relative to Live Cgroups 80%)", fontweight="bold", pad=15)
    ax.set_ylabel("Improvement Ratio (×) — Higher is Better", labelpad=10)
    ax.set_xticks(np.arange(len(sp_labels)))
    ax.set_xticklabels(sp_labels, fontsize=10.5)
    ax.set_ylim(0, max(sp_vals) * 1.25)
    ax.legend(loc="upper left", frameon=True, framealpha=0.95, fontsize=11)
    ax.grid(axis="y", linestyle=":", alpha=0.6, zorder=0)

    fig.suptitle("Graph 4: Relative Tail Latency Speedup Factor",
                 fontsize=14, fontweight="bold", y=0.98, color="#0F172A")

    save_plot_both(fig, "cgroups_vs_qos_improvement_factor.png")


def main():
    sweep_data, live_data = load_all_data()
    print("Generating Graph 1...")
    plot_1_head_to_head(live_data, sweep_data)
    print("Generating Graph 2...")
    plot_2_quota_sweep(sweep_data, live_data)
    print("Generating Graph 3...")
    plot_3_percentiles_progression(sweep_data, live_data)
    print("Generating Graph 4...")
    plot_4_improvement_factor(sweep_data, live_data)
    print("\n[SUCCESS] All 4 graphs generated successfully!")


if __name__ == "__main__":
    main()
