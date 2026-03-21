#!/usr/bin/env python3
import csv
import os
from collections import defaultdict

import matplotlib.pyplot as plt

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RESULTS_CSV = os.path.join(ROOT_DIR, "results", "processed", "results.csv")
RAW_DIR = os.path.join(ROOT_DIR, "results", "raw")
OUT_DIR = os.path.join(ROOT_DIR, "results", "processed")


def read_results(path):
    rows = []
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing results file: {path}")

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                r["p99_ms"] = float(r["p99_ms"])
                r["containers"] = int(r["containers"])
            except Exception:
                continue
            rows.append(r)
    return rows


def avg_by(rows, key_fn, value_key="p99_ms"):
    acc = defaultdict(list)
    for r in rows:
        acc[key_fn(r)].append(float(r[value_key]))
    return {k: (sum(v) / len(v)) for k, v in acc.items() if v}


def plot_p99_vs_noise(rows):
    order = ["low", "medium", "high"]
    baseline = [r for r in rows if r["ebpf_enabled"] == "false"]
    ebpf = [r for r in rows if r["ebpf_enabled"] == "true"]

    b = avg_by(baseline, lambda r: r["noise_level"])
    e = avg_by(ebpf, lambda r: r["noise_level"])

    x = list(range(len(order)))
    bvals = [b.get(o, 0.0) for o in order]
    evals = [e.get(o, 0.0) for o in order]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(x, bvals, marker="o", label="Baseline")
    ax.plot(x, evals, marker="o", label="eBPF")
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


def load_latency_samples(prefix):
    vals = []
    p = os.path.join(RAW_DIR, prefix)
    if not os.path.exists(p):
        return vals
    with open(p, "r", encoding="utf-8") as f:
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


def plot_latency_histogram(rows):
    baseline_vals = []
    ebpf_vals = []
    for r in rows:
        lat_file = f"{r['experiment']}_{r['tenant']}.latency"
        vals = load_latency_samples(lat_file)
        if r["ebpf_enabled"] == "true":
            ebpf_vals.extend(vals)
        else:
            baseline_vals.extend(vals)

    fig, ax = plt.subplots(figsize=(8, 5))
    if baseline_vals:
        ax.hist(baseline_vals, bins=40, alpha=0.5, label="Baseline")
    if ebpf_vals:
        ax.hist(ebpf_vals, bins=40, alpha=0.5, label="eBPF")
    ax.set_xlabel("Latency (ms)")
    ax.set_ylabel("Frequency")
    ax.set_title("Latency Distribution Histogram")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    out = os.path.join(OUT_DIR, "latency_histogram.png")
    fig.savefig(out, dpi=150)
    print(f"Saved: {out}")


def plot_baseline_vs_ebpf(rows):
    by_scenario = avg_by(rows, lambda r: "eBPF" if r["ebpf_enabled"] == "true" else "Baseline")
    labels = ["Baseline", "eBPF"]
    vals = [by_scenario.get(x, 0.0) for x in labels]

    fig, ax = plt.subplots(figsize=(7, 5))
    ax.bar(labels, vals, color=["#c44e52", "#55a868"])
    ax.set_ylabel("Average p99 latency (ms)")
    ax.set_title("Baseline vs eBPF")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    out = os.path.join(OUT_DIR, "baseline_vs_ebpf.png")
    fig.savefig(out, dpi=150)
    print(f"Saved: {out}")


def plot_scalability(rows):
    b = avg_by([r for r in rows if r["ebpf_enabled"] == "false"], lambda r: r["containers"])
    e = avg_by([r for r in rows if r["ebpf_enabled"] == "true"], lambda r: r["containers"])

    xs = sorted(set(list(b.keys()) + list(e.keys())))
    bvals = [b.get(x, 0.0) for x in xs]
    evals = [e.get(x, 0.0) for x in xs]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(xs, bvals, marker="o", label="Baseline")
    ax.plot(xs, evals, marker="o", label="eBPF")
    ax.set_xlabel("Container count")
    ax.set_ylabel("Average p99 latency (ms)")
    ax.set_title("Scalability: Containers vs p99")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    out = os.path.join(OUT_DIR, "scalability.png")
    fig.savefig(out, dpi=150)
    print(f"Saved: {out}")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = read_results(RESULTS_CSV)
    plot_p99_vs_noise(rows)
    plot_latency_histogram(rows)
    plot_baseline_vs_ebpf(rows)
    plot_scalability(rows)


if __name__ == "__main__":
    main()
