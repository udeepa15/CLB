#!/usr/bin/env python3
import csv
import os
from collections import defaultdict

import matplotlib.pyplot as plt

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SUMMARY_CSV = os.path.join(ROOT_DIR, "results", "processed", "summary.csv")
OUT_PNG = os.path.join(ROOT_DIR, "results", "processed", "p99_comparison.png")


def read_summary(path):
    rows = []
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing summary file: {path}")

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                row["p99_ms"] = float(row["p99_ms"])
            except Exception:
                continue
            rows.append(row)
    return rows


def aggregate(rows):
    data = defaultdict(list)
    for r in rows:
        key = (r["scenario"], r["tenant"])
        data[key].append(r["p99_ms"])

    avg = {}
    for key, vals in data.items():
        avg[key] = sum(vals) / len(vals)
    return avg


def plot(avg, out_png):
    tenants = ["tenant1", "tenant2"]
    scenarios = ["baseline", "ebpf"]

    x = list(range(len(tenants)))
    width = 0.35

    baseline_vals = [avg.get(("baseline", t), 0.0) for t in tenants]
    ebpf_vals = [avg.get(("ebpf", t), 0.0) for t in tenants]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar([v - width / 2 for v in x], baseline_vals, width=width, label="Baseline")
    ax.bar([v + width / 2 for v in x], ebpf_vals, width=width, label="eBPF Enabled")

    ax.set_xticks(x)
    ax.set_xticklabels(tenants)
    ax.set_ylabel("p99 latency (ms)")
    ax.set_title("Noisy Neighbor Mitigation with tc/eBPF")
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.3)

    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_png, dpi=150)
    print(f"Saved: {out_png}")


def main():
    rows = read_summary(SUMMARY_CSV)
    avg = aggregate(rows)
    plot(avg, OUT_PNG)


if __name__ == "__main__":
    main()
