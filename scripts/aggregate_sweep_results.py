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


def extract_sweep_params(dirpath: str) -> tuple[int, int, str] | None:
    """Extract tenants and rate from sweep dir name like t<tenants>_r<rate>_<timestamp>."""
    match = re.search(r"t(\d+)_r(\d+)_(\d+)$", os.path.basename(dirpath))
    if match:
        return int(match.group(1)), int(match.group(2)), match.group(3)
    return None


def main():
    args = parse_args()
    rows = []

    # walk all sweep subdirectories
    for dirpath, _, filenames in os.walk(args.results_dir):
        sweep_params = extract_sweep_params(dirpath)
        if not sweep_params:
            continue
        tenants, rate, timestamp = sweep_params

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
        "timestamp", "tenant_count", "attacker_rate", "worker",
        "completed", "errors", "duration_sec", "throughput_mps"
    ]
    with open(args.output_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(rows, key=lambda r: (r["tenant_count"], r["attacker_rate"])):
            writer.writerow(row)

    print(f"Wrote {len(rows)} rows to {args.output_csv}")


if __name__ == "__main__":
    main()
