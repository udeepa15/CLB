#!/usr/bin/env python3
from __future__ import annotations

import csv
import math
import os
import shutil
from collections import defaultdict
from statistics import mean, stdev

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PROCESSED_RESULTS = os.path.join(ROOT, "results", "processed", "results.csv")
FINAL_RESULTS = os.path.join(ROOT, "results", "final", "results.csv")
FINAL_SUMMARY = os.path.join(ROOT, "results", "final", "summary.csv")
OUT_DIR = os.path.join(ROOT, "results", "excel_package")


def _float(value: str, default: float = float("nan")) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return stdev(values)


def _metric_summary(rows: list[dict], metric: str) -> dict[str, float]:
    vals = [_float(r.get(metric, "nan")) for r in rows]
    vals = [v for v in vals if math.isfinite(v)]
    if not vals:
        return {
            f"{metric}_mean": float("nan"),
            f"{metric}_std": float("nan"),
            f"{metric}_min": float("nan"),
            f"{metric}_max": float("nan"),
        }
    return {
        f"{metric}_mean": mean(vals),
        f"{metric}_std": _safe_stdev(vals),
        f"{metric}_min": min(vals),
        f"{metric}_max": max(vals),
    }


def _load_csv(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(path: str, rows: list[dict], fieldnames: list[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _pick_results_source() -> str:
    if os.path.exists(PROCESSED_RESULTS):
        return PROCESSED_RESULTS
    if os.path.exists(FINAL_RESULTS):
        return FINAL_RESULTS
    raise FileNotFoundError("No results.csv found in processed/ or final/")


def export_package() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)

    source_results = _pick_results_source()
    rows = _load_csv(source_results)
    if not rows:
        raise RuntimeError("Results file is empty; cannot build Excel package")

    shutil.copyfile(source_results, os.path.join(OUT_DIR, "raw_results_full.csv"))

    if os.path.exists(FINAL_SUMMARY):
        shutil.copyfile(FINAL_SUMMARY, os.path.join(OUT_DIR, "final_summary_by_scenario.csv"))

    grouped_method: dict[str, list[dict]] = defaultdict(list)
    grouped_method_noise: dict[tuple[str, str], list[dict]] = defaultdict(list)
    grouped_method_failure: dict[tuple[str, str], list[dict]] = defaultdict(list)
    grouped_method_traffic: dict[tuple[str, str], list[dict]] = defaultdict(list)
    grouped_experiment: dict[str, list[dict]] = defaultdict(list)

    for r in rows:
        method = r.get("isolation_method", "unknown")
        noise = r.get("noise_level", "unknown")
        failure = r.get("failure_mode", "unknown")
        traffic = r.get("traffic_pattern", "unknown")
        exp = r.get("experiment", "unknown")

        grouped_method[method].append(r)
        grouped_method_noise[(method, noise)].append(r)
        grouped_method_failure[(method, failure)].append(r)
        grouped_method_traffic[(method, traffic)].append(r)
        grouped_experiment[exp].append(r)

    tracked_metrics = [
        "p50_ms",
        "p95_ms",
        "p99_ms",
        "jitter_ms",
        "throughput_rps",
        "packet_drops",
        "tail_amplification",
        "isolation_score",
    ]

    def build_group_rows(groups: dict, key_names: list[str]) -> list[dict]:
        out = []
        for key, grows in sorted(groups.items(), key=lambda x: str(x[0])):
            row = {}
            key_tuple = key if isinstance(key, tuple) else (key,)
            for i, name in enumerate(key_names):
                row[name] = key_tuple[i] if i < len(key_tuple) else ""
            row["samples"] = len(grows)
            row["unique_experiments"] = len({g.get("experiment", "") for g in grows})
            row["unique_tenants"] = len({g.get("tenant", "") for g in grows})
            for metric in tracked_metrics:
                row.update(_metric_summary(grows, metric))
            out.append(row)
        return out

    method_rows = build_group_rows(grouped_method, ["isolation_method"])
    method_noise_rows = build_group_rows(grouped_method_noise, ["isolation_method", "noise_level"])
    method_failure_rows = build_group_rows(grouped_method_failure, ["isolation_method", "failure_mode"])
    method_traffic_rows = build_group_rows(grouped_method_traffic, ["isolation_method", "traffic_pattern"])

    common_summary_fields = [
        "samples",
        "unique_experiments",
        "unique_tenants",
    ]
    metric_fields = []
    for m in tracked_metrics:
        metric_fields.extend([f"{m}_mean", f"{m}_std", f"{m}_min", f"{m}_max"])

    _write_csv(
        os.path.join(OUT_DIR, "method_overview.csv"),
        method_rows,
        ["isolation_method"] + common_summary_fields + metric_fields,
    )
    _write_csv(
        os.path.join(OUT_DIR, "method_by_noise.csv"),
        method_noise_rows,
        ["isolation_method", "noise_level"] + common_summary_fields + metric_fields,
    )
    _write_csv(
        os.path.join(OUT_DIR, "method_by_failure_mode.csv"),
        method_failure_rows,
        ["isolation_method", "failure_mode"] + common_summary_fields + metric_fields,
    )
    _write_csv(
        os.path.join(OUT_DIR, "method_by_traffic_pattern.csv"),
        method_traffic_rows,
        ["isolation_method", "traffic_pattern"] + common_summary_fields + metric_fields,
    )

    experiment_rows = []
    for exp, grows in sorted(grouped_experiment.items(), key=lambda x: x[0]):
        first = grows[0]
        out = {
            "experiment": exp,
            "timestamp_first": first.get("timestamp", ""),
            "noise_level": first.get("noise_level", ""),
            "traffic_pattern": first.get("traffic_pattern", ""),
            "isolation_method": first.get("isolation_method", ""),
            "identity_mode": first.get("identity_mode", ""),
            "strategy": first.get("strategy", ""),
            "containers": first.get("containers", ""),
            "failure_mode": first.get("failure_mode", ""),
            "iteration": first.get("iteration", ""),
            "samples": len(grows),
            "unique_tenants": len({g.get("tenant", "") for g in grows}),
        }
        for metric in tracked_metrics:
            out.update(_metric_summary(grows, metric))
        experiment_rows.append(out)

    experiment_fields = [
        "experiment",
        "timestamp_first",
        "noise_level",
        "traffic_pattern",
        "isolation_method",
        "identity_mode",
        "strategy",
        "containers",
        "failure_mode",
        "iteration",
        "samples",
        "unique_tenants",
    ] + metric_fields
    _write_csv(os.path.join(OUT_DIR, "experiment_rollup.csv"), experiment_rows, experiment_fields)

    sorted_p99 = sorted(rows, key=lambda r: _float(r.get("p99_ms", "nan")), reverse=True)
    top_rows = []
    for r in sorted_p99[:200]:
        top_rows.append(
            {
                "timestamp": r.get("timestamp", ""),
                "experiment": r.get("experiment", ""),
                "tenant": r.get("tenant", ""),
                "noise_level": r.get("noise_level", ""),
                "traffic_pattern": r.get("traffic_pattern", ""),
                "isolation_method": r.get("isolation_method", ""),
                "identity_mode": r.get("identity_mode", ""),
                "strategy": r.get("strategy", ""),
                "containers": r.get("containers", ""),
                "failure_mode": r.get("failure_mode", ""),
                "p50_ms": r.get("p50_ms", ""),
                "p95_ms": r.get("p95_ms", ""),
                "p99_ms": r.get("p99_ms", ""),
                "jitter_ms": r.get("jitter_ms", ""),
                "throughput_rps": r.get("throughput_rps", ""),
                "tail_amplification": r.get("tail_amplification", ""),
                "isolation_score": r.get("isolation_score", ""),
            }
        )

    top_fields = [
        "timestamp",
        "experiment",
        "tenant",
        "noise_level",
        "traffic_pattern",
        "isolation_method",
        "identity_mode",
        "strategy",
        "containers",
        "failure_mode",
        "p50_ms",
        "p95_ms",
        "p99_ms",
        "jitter_ms",
        "throughput_rps",
        "tail_amplification",
        "isolation_score",
    ]
    _write_csv(os.path.join(OUT_DIR, "top_200_worst_p99_cases.csv"), top_rows, top_fields)

    outlier_threshold = 8.0
    outlier_rows = [
        r
        for r in rows
        if math.isfinite(_float(r.get("p99_ms", "nan"))) and _float(r.get("p99_ms", "nan")) >= outlier_threshold
    ]
    outlier_rows_sorted = sorted(outlier_rows, key=lambda r: _float(r.get("p99_ms", "nan")), reverse=True)
    _write_csv(os.path.join(OUT_DIR, "outlier_cases_p99_ge_8ms.csv"), outlier_rows_sorted, list(rows[0].keys()))

    dictionary_rows = [
        {"column_name": "timestamp", "meaning": "Run timestamp for this sample row."},
        {"column_name": "experiment", "meaning": "Unique experiment/scenario identifier."},
        {"column_name": "noise_level", "meaning": "Noise intensity level: low, medium, high."},
        {"column_name": "traffic_pattern", "meaning": "Background traffic shape: constant, bursty, delayed_start, intermittent."},
        {"column_name": "isolation_method", "meaning": "Isolation mode: none, tc, ebpf, adaptive."},
        {"column_name": "identity_mode", "meaning": "Classification key used for policy: ip or cgroup."},
        {"column_name": "strategy", "meaning": "Specific strategy used by the isolation method."},
        {"column_name": "containers", "meaning": "Container count in that scenario."},
        {"column_name": "failure_mode", "meaning": "Failure injection: none, spike, churn."},
        {"column_name": "tenant", "meaning": "Tenant identifier for this row."},
        {"column_name": "p50_ms", "meaning": "Median latency in milliseconds."},
        {"column_name": "p95_ms", "meaning": "95th percentile latency in milliseconds."},
        {"column_name": "p99_ms", "meaning": "99th percentile latency in milliseconds."},
        {"column_name": "jitter_ms", "meaning": "Latency jitter metric in milliseconds."},
        {"column_name": "throughput_rps", "meaning": "Throughput in requests per second."},
        {"column_name": "packet_drops", "meaning": "Observed packet drops metric for that sample."},
        {"column_name": "tail_amplification", "meaning": "Tail inflation ratio computed as p99/p50."},
        {"column_name": "isolation_score", "meaning": "Custom isolation score from your pipeline."},
    ]
    _write_csv(
        os.path.join(OUT_DIR, "data_dictionary.csv"),
        dictionary_rows,
        ["column_name", "meaning"],
    )

    readme_path = os.path.join(OUT_DIR, "README.md")
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write("# Excel Package for Supervisor Review\n\n")
        f.write("This folder contains Excel-ready CSV files derived from your latest results dataset.\n\n")
        f.write("## Files\n")
        f.write("- raw_results_full.csv: all sample rows from results.csv\n")
        f.write("- final_summary_by_scenario.csv: scenario-level summary from results/final/summary.csv (if present)\n")
        f.write("- method_overview.csv: method-level aggregates\n")
        f.write("- method_by_noise.csv: method x noise aggregates\n")
        f.write("- method_by_failure_mode.csv: method x failure-mode aggregates\n")
        f.write("- method_by_traffic_pattern.csv: method x traffic-pattern aggregates\n")
        f.write("- experiment_rollup.csv: experiment-level rollup\n")
        f.write("- top_200_worst_p99_cases.csv: worst tail-latency samples\n")
        f.write("- outlier_cases_p99_ge_8ms.csv: all rows with p99 >= 8 ms\n")
        f.write("- data_dictionary.csv: field definitions\n\n")
        f.write("## Suggested Excel Workbook Tabs\n")
        f.write("1. Raw_Data\n")
        f.write("2. Method_Overview\n")
        f.write("3. Method_By_Noise\n")
        f.write("4. Method_By_Failure\n")
        f.write("5. Method_By_Traffic\n")
        f.write("6. Experiment_Rollup\n")
        f.write("7. Worst_Cases\n")
        f.write("8. Outliers_8ms\n")
        f.write("9. Data_Dictionary\n")

    print(f"Created Excel package in: {OUT_DIR}")


if __name__ == "__main__":
    export_package()
