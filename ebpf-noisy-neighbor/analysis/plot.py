#!/usr/bin/env python3
from __future__ import annotations

import csv
import math
import os
from collections import defaultdict

import matplotlib.pyplot as plt

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RESULTS_CSV = os.path.join(ROOT_DIR, "results", "processed", "results.csv")
RAW_DIR = os.path.join(ROOT_DIR, "results", "raw")
OUT_DIR = os.path.join(ROOT_DIR, "results", "processed")


def _f(v, default=float("nan")):
    try:
        return float(v)
    except Exception:
        return default


def read_results(path):
    rows = []
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing results file: {path}")

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            r["p99_ms"] = _f(r.get("p99_ms"))
            r["tail_amplification"] = _f(r.get("tail_amplification"))
            r["jitter_ms"] = _f(r.get("jitter_ms"))
            r["throughput_rps"] = _f(r.get("throughput_rps"))
            r["packet_drops"] = _f(r.get("packet_drops"), 0.0)
            try:
                r["containers"] = int(r.get("containers", 0))
            except Exception:
                r["containers"] = 0
            rows.append(r)
    return rows


def avg_by(rows, key_fn, value_key="p99_ms"):
    acc = defaultdict(list)
    for r in rows:
        v = _f(r.get(value_key, "nan"))
        if math.isfinite(v):
            acc[key_fn(r)].append(v)
    return {k: (sum(v) / len(v)) for k, v in acc.items() if v}


def load_latency_samples(path):
    vals = []
    if not os.path.exists(path):
        return vals
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.strip().rsplit(",", 1)
            if len(parts) != 2:
                continue
            try:
                vals.append(float(parts[1]))
            except Exception:
                pass
    return vals


def load_timeseries(path):
    xs, ys = [], []
    if not os.path.exists(path):
        return xs, ys
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                xs.append(float(row["index"]))
                ys.append(float(row["latency_ms"]))
            except Exception:
                pass
    return xs, ys


def plot_latency_cdf(rows):
    groups = {
        "no_isolation": [],
        "tc": [],
        "ebpf": [],
        "adaptive": [],
    }

    used_raw_samples = False

    def _bucket_for_method(method):
        if method == "none":
            return "no_isolation"
        if method == "tc":
            return "tc"
        if method == "adaptive":
            return "adaptive"
        return "ebpf"

    for r in rows:
        lat_file = os.path.join(RAW_DIR, f"{r['experiment']}_{r['tenant']}.latency")
        vals = load_latency_samples(lat_file)
        bucket = _bucket_for_method(r.get("isolation_method", "none"))
        groups[bucket].extend(vals)
        if vals:
            used_raw_samples = True

    # Fallback path: if raw per-request latency files are unavailable,
    # build an empirical CDF from per-run p99 values in results.csv.
    if not used_raw_samples:
        for r in rows:
            bucket = _bucket_for_method(r.get("isolation_method", "none"))
            v = _f(r.get("p99_ms"))
            if math.isfinite(v):
                groups[bucket].append(v)

    fig, ax = plt.subplots(figsize=(8, 5))
    for label, vals in groups.items():
        if not vals:
            continue
        vals = sorted(vals)
        y = [i / len(vals) for i in range(1, len(vals) + 1)]
        ax.plot(vals, y, label=label)

    ax.set_xlabel("Latency (ms)")
    ax.set_ylabel("CDF")
    if used_raw_samples:
        ax.set_title("Latency CDF by Isolation Method")
    else:
        ax.set_title("Latency CDF by Isolation Method (from p99 snapshots)")
    ax.grid(True, alpha=0.3)
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend()
    fig.tight_layout()
    out = os.path.join(OUT_DIR, "latency_cdf.png")
    fig.savefig(out, dpi=150)
    print(f"Saved: {out}")


def plot_p99_vs_noise(rows):
    order = ["low", "medium", "high"]
    methods = ["none", "tc", "ebpf", "adaptive"]
    x = list(range(len(order)))

    fig, ax = plt.subplots(figsize=(9, 5))
    for method in methods:
        d = avg_by([r for r in rows if r.get("isolation_method") == method], lambda r: r.get("noise_level", "medium"))
        vals = [d.get(o, float("nan")) for o in order]
        ax.plot(x, vals, marker="o", label=method)

    ax.set_xticks(x)
    ax.set_xticklabels(order)
    ax.set_xlabel("Noise level")
    ax.set_ylabel("Average p99 latency (ms)")
    ax.set_title("p99 vs Noise Level")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    out = os.path.join(OUT_DIR, "p99_vs_noise.png")
    fig.savefig(out, dpi=150)
    print(f"Saved: {out}")


def plot_isolation_effectiveness(rows):
    vals = avg_by(rows, lambda r: r.get("isolation_method", "none"), value_key="tail_amplification")
    labels = ["none", "tc", "ebpf", "adaptive"]
    ys = [vals.get(k, float("nan")) for k in labels]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(labels, ys, color=["#c44e52", "#8172b3", "#4c72b0", "#55a868"])
    ax.set_ylabel("Avg tail amplification (p99/p50)")
    ax.set_title("Isolation Effectiveness (Lower is Better)")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    out = os.path.join(OUT_DIR, "isolation_effectiveness.png")
    fig.savefig(out, dpi=150)
    print(f"Saved: {out}")


def plot_overhead_tradeoff(rows):
    groups = defaultdict(lambda: {"drops": [], "p99": []})
    for r in rows:
        m = r.get("isolation_method", "none")
        if math.isfinite(r["packet_drops"]):
            groups[m]["drops"].append(r["packet_drops"])
        if math.isfinite(r["p99_ms"]):
            groups[m]["p99"].append(r["p99_ms"])

    fig, ax = plt.subplots(figsize=(8, 5))
    for method, v in groups.items():
        if not v["drops"] or not v["p99"]:
            continue
        x = sum(v["drops"]) / len(v["drops"])
        y = sum(v["p99"]) / len(v["p99"])
        ax.scatter([x], [y], s=70, label=method)
        ax.annotate(method, (x, y), textcoords="offset points", xytext=(5, 5))

    ax.set_xlabel("Average packet drops")
    ax.set_ylabel("Average p99 latency (ms)")
    ax.set_title("Overhead vs Performance Trade-off")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    out = os.path.join(OUT_DIR, "overhead_vs_performance.png")
    fig.savefig(out, dpi=150)
    print(f"Saved: {out}")


def plot_time_series(rows):
    candidate = None
    for r in rows:
        if r.get("isolation_method") == "adaptive":
            candidate = r
            break
    if candidate is None and rows:
        candidate = rows[0]
    if candidate is None:
        return

    ts = os.path.join(RAW_DIR, f"{candidate['experiment']}_{candidate['tenant']}.timeseries.csv")
    xs, ys = load_timeseries(ts)
    if not xs:
        return

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(xs, ys, linewidth=1.2)
    ax.set_xlabel("Request index")
    ax.set_ylabel("Latency (ms)")
    ax.set_title(f"Time-series Latency: {candidate['experiment']} / {candidate['tenant']}")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    out = os.path.join(OUT_DIR, "latency_time_series.png")
    fig.savefig(out, dpi=150)
    print(f"Saved: {out}")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = read_results(RESULTS_CSV)

    plot_latency_cdf(rows)
    plot_p99_vs_noise(rows)
    plot_isolation_effectiveness(rows)
    plot_overhead_tradeoff(rows)
    plot_time_series(rows)


if __name__ == "__main__":
    main()
