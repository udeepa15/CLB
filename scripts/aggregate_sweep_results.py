#!/usr/bin/env python3
"""
Aggregate RESULT: lines from sweep worker logs into a CSV.
Usage: aggregate_sweep_results.py --results-dir ebpf_research/results
"""
from __future__ import annotations
import argparse
import csv
import os
import re
import sys


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--results-dir", required=True, help="Path to results directory with sweep subdirs")
    p.add_argument("--output-csv", default="sweep_aggregate.csv")
    return p.parse_args()


def parse_result_line(line: str) -> dict | None:
    """Parse a RESULT: line into a dict."""
    # Format: RESULT: completed=<int> errors=<int> duration_sec=<float> throughput_mps=<float>
    match = re.search(
        r"RESULT:\s+completed=(\d+)\s+errors=(\d+)\s+duration_sec=([\d.]+)\s+throughput_mps=([\d.]+)",
        line
    )
    if not match:
        return None
    return {
        "completed": int(match.group(1)),
        "errors": int(match.group(2)),
        "duration_sec": float(match.group(3)),
        "throughput_mps": float(match.group(4)),
    }


def extract_sweep_params(dirpath: str) -> tuple[str, int, int, str] | None:
    """Extract mode, tenants, and rate from sweep dir name."""
    base = os.path.basename(dirpath)
    match = re.search(r"m([^_]+)_t(\d+)_r(\d+)_(\d+)$", base)
    if match:
        return match.group(1), int(match.group(2)), int(match.group(3)), match.group(4)

    match = re.search(r"t(\d+)_r(\d+)_(\d+)$", base)
    if match:
        return "unknown", int(match.group(1)), int(match.group(2)), match.group(3)
    return None


def main():
    args = parse_args()
    rows = []

    # walk all sweep subdirectories (skip archived runs)
    for dirpath, dirnames, filenames in os.walk(args.results_dir):
        dirnames[:] = [d for d in dirnames if not d.startswith("archive_")]
        if os.path.basename(dirpath).startswith("archive_"):
            continue
        sweep_params = extract_sweep_params(dirpath)
        if not sweep_params:
            continue
        mode, tenants, rate, timestamp = sweep_params

        # parse worker logs in this directory
        for fname in filenames:
            if fname.startswith("worker_") and fname.endswith(".log"):
                fpath = os.path.join(dirpath, fname)
                try:
                    with open(fpath) as f:
                        for line in f:
                            result = parse_result_line(line)
                            if result:
                                rows.append({
                                    "timestamp": timestamp,
                                    "mode": mode,
                                    "tenant_count": tenants,
                                    "attacker_rate": rate,
                                    "worker": fname.replace("worker_", "").replace(".log", ""),
                                    "completed": result["completed"],
                                    "errors": result["errors"],
                                    "duration_sec": result["duration_sec"],
                                    "throughput_mps": result["throughput_mps"],
                                })
                except Exception as e:
                    print(f"Error reading {fpath}: {e}", file=sys.stderr)

    if not rows:
        print("No RESULT: lines found", file=sys.stderr)
        return

    # write CSV
    fieldnames = [
        "timestamp", "mode", "tenant_count", "attacker_rate", "worker",
        "completed", "errors", "duration_sec", "throughput_mps"
    ]
    with open(args.output_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(rows, key=lambda r: (r["mode"], r["tenant_count"], r["attacker_rate"])):
            writer.writerow(row)

    print(f"Wrote {len(rows)} rows to {args.output_csv}")


if __name__ == "__main__":
    main()
