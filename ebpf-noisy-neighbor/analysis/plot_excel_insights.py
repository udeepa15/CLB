#!/usr/bin/env python3
from __future__ import annotations

import csv
import math
import os
from collections import defaultdict

import matplotlib.pyplot as plt

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXCEL_DIR = os.path.join(ROOT_DIR, "results", "excel_package")
OUT_DIR = os.path.join(ROOT_DIR, "results", "processed", "excel_insights")


def read_csv(path):
    with open(path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fnum(v, default=float("nan")):
    try:
        return float(v)
    except Exception:
        return default


def weighted_mean(rows, value_key, weight_key):
    num = 0.0
    den = 0.0
    for r in rows:
        v = fnum(r.get(value_key))
        w = fnum(r.get(weight_key), 0.0)
        if math.isfinite(v) and w > 0:
            num += v * w
            den += w
    return num / den if den else float("nan")


def plot_p99_heatmaps(final_rows):
    methods = ["none", "tc", "ebpf", "adaptive"]
    traffic = ["constant", "bursty", "intermittent", "delayed_start"]
    noises = ["low", "medium", "high"]

    grouped = defaultdict(list)
    for r in final_rows:
        key = (r.get("noise_level"), r.get("traffic_pattern"), r.get("isolation_method"))
        grouped[key].append(r)

    matrix = {}
    for n in noises:
        mtx = []
        for t in traffic:
            row = []
            for m in methods:
                rows = grouped.get((n, t, m), [])
                row.append(weighted_mean(rows, "avg_p99_ms", "runs"))
            mtx.append(row)
        matrix[n] = mtx

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.8), sharey=True)
    vmin = min(
        v
        for n in noises
        for r in matrix[n]
        for v in r
        if math.isfinite(v)
    )
    vmax = max(
        v
        for n in noises
        for r in matrix[n]
        for v in r
        if math.isfinite(v)
    )

    for ax, n in zip(axes, noises):
        im = ax.imshow(matrix[n], aspect="auto", vmin=vmin, vmax=vmax, cmap="viridis")
        ax.set_title(f"Noise: {n}")
        ax.set_xticks(range(len(methods)))
        ax.set_xticklabels(methods)
        ax.set_yticks(range(len(traffic)))
        ax.set_yticklabels(traffic)

        for i, r in enumerate(matrix[n]):
            for j, v in enumerate(r):
                if math.isfinite(v):
                    ax.text(j, i, f"{v:.2f}", ha="center", va="center", color="white", fontsize=8)

    cbar = fig.colorbar(im, ax=axes.ravel().tolist(), shrink=0.88)
    cbar.set_label("avg p99 latency (ms)")
    fig.suptitle("p99 heatmap by noise / traffic / isolation method")
    fig.subplots_adjust(top=0.84, wspace=0.18)
    out = os.path.join(OUT_DIR, "p99_heatmap_by_noise_and_traffic.png")
    fig.savefig(out, dpi=170)
    print(f"Saved: {out}")


def plot_method_overview_bars(overview_rows):
    order = ["none", "tc", "ebpf", "adaptive"]
    rows_by_method = {r.get("isolation_method"): r for r in overview_rows}

    methods = [m for m in order if m in rows_by_method]
    p99 = [fnum(rows_by_method[m].get("p99_ms_mean")) for m in methods]
    thr = [fnum(rows_by_method[m].get("throughput_rps_mean")) for m in methods]
    scores = [fnum(rows_by_method[m].get("isolation_score_mean")) for m in methods]

    x = list(range(len(methods)))

    fig, ax1 = plt.subplots(figsize=(9, 5))
    w = 0.36
    ax1.bar([i - w / 2 for i in x], p99, width=w, label="avg p99 (ms)", color="#4c78a8")
    ax1.bar([i + w / 2 for i in x], thr, width=w, label="avg throughput (rps)", color="#72b7b2")
    ax1.set_xticks(x)
    ax1.set_xticklabels(methods)
    ax1.set_ylabel("value")
    ax1.set_title("Method overview: latency vs throughput")
    ax1.grid(axis="y", alpha=0.25)

    ax2 = ax1.twinx()
    ax2.plot(x, scores, marker="o", color="#e45756", label="isolation score")
    ax2.set_ylabel("isolation score")

    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper left")

    fig.tight_layout()
    out = os.path.join(OUT_DIR, "method_overview_latency_throughput_score.png")
    fig.savefig(out, dpi=170)
    print(f"Saved: {out}")


def plot_p99_distribution(raw_rows):
    order = ["none", "tc", "ebpf", "adaptive"]
    vals = defaultdict(list)
    for r in raw_rows:
        m = r.get("isolation_method", "none")
        v = fnum(r.get("p99_ms"))
        if math.isfinite(v):
            vals[m].append(v)

    methods = [m for m in order if vals[m]]
    data = [vals[m] for m in methods]

    fig, ax = plt.subplots(figsize=(9, 5))
    bp = ax.boxplot(data, patch_artist=True, tick_labels=methods, showfliers=False)
    palette = ["#c44e52", "#8172b3", "#4c72b0", "#55a868"]
    for patch, color in zip(bp["boxes"], palette[: len(methods)]):
        patch.set_facecolor(color)
        patch.set_alpha(0.6)

    ax.set_ylabel("p99 latency (ms)")
    ax.set_title("Distribution of p99 latency by isolation method")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    out = os.path.join(OUT_DIR, "p99_distribution_boxplot_by_method.png")
    fig.savefig(out, dpi=170)
    print(f"Saved: {out}")


def plot_grouped_bar(rows, category_key, value_key, title, out_name):
    methods = ["none", "tc", "ebpf", "adaptive"]
    categories = sorted({r.get(category_key, "unknown") for r in rows})

    lookup = defaultdict(dict)
    for r in rows:
        method = r.get("isolation_method")
        cat = r.get(category_key)
        if method and cat:
            lookup[method][cat] = fnum(r.get(value_key))

    fig, ax = plt.subplots(figsize=(10, 5))
    x = list(range(len(categories)))
    width = 0.18
    offsets = {
        "none": -1.5 * width,
        "tc": -0.5 * width,
        "ebpf": 0.5 * width,
        "adaptive": 1.5 * width,
    }
    colors = {
        "none": "#c44e52",
        "tc": "#8172b3",
        "ebpf": "#4c72b0",
        "adaptive": "#55a868",
    }

    for m in methods:
        ys = [lookup[m].get(c, float("nan")) for c in categories]
        ax.bar([i + offsets[m] for i in x], ys, width=width, label=m, color=colors[m])

    ax.set_xticks(x)
    ax.set_xticklabels(categories, rotation=15)
    ax.set_ylabel("avg p99 latency (ms)")
    ax.set_title(title)
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    fig.tight_layout()
    out = os.path.join(OUT_DIR, out_name)
    fig.savefig(out, dpi=170)
    print(f"Saved: {out}")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    final_rows = read_csv(os.path.join(EXCEL_DIR, "final_summary_by_scenario.csv"))
    overview_rows = read_csv(os.path.join(EXCEL_DIR, "method_overview.csv"))
    raw_rows = read_csv(os.path.join(EXCEL_DIR, "raw_results_full.csv"))
    by_failure_rows = read_csv(os.path.join(EXCEL_DIR, "method_by_failure_mode.csv"))
    by_traffic_rows = read_csv(os.path.join(EXCEL_DIR, "method_by_traffic_pattern.csv"))

    plot_p99_heatmaps(final_rows)
    plot_method_overview_bars(overview_rows)
    plot_p99_distribution(raw_rows)
    plot_grouped_bar(
        by_failure_rows,
        category_key="failure_mode",
        value_key="p99_ms_mean",
        title="p99 by failure mode and isolation method",
        out_name="p99_by_failure_mode.png",
    )
    plot_grouped_bar(
        by_traffic_rows,
        category_key="traffic_pattern",
        value_key="p99_ms_mean",
        title="p99 by traffic pattern and isolation method",
        out_name="p99_by_traffic_pattern.png",
    )


if __name__ == "__main__":
    main()
