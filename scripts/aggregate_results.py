#!/usr/bin/env python3
"""
Aggregate worker RESULT lines from sweep logs into a CSV.

This reads both the newer mode-tagged layout:
ebpf_research/results/msidecar*_t*_r*_<timestamp>/worker_*.log

and the older legacy layout:
ebpf_research/results/t*_r*_<timestamp>/worker_*.log
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
RESULT_DIR = REPO_ROOT / "ebpf_research" / "results"
OUT_CSV = RESULT_DIR / "sweep_results_v2.csv"

NEW_SWEEP_DIR_RE = re.compile(
    r"m(?P<mode>.+?)_t(?P<tenants>\d+)_r(?P<rate>\d+)_(?P<ts>\d+)$"
)
LEGACY_SWEEP_DIR_RE = re.compile(
    r"t(?P<tenants>\d+)_r(?P<rate>\d+)_(?P<ts>\d+)$"
)
RESULT_RE = re.compile(
    r"RESULT:\s+completed=(?P<completed>\d+)\s+errors=(?P<errors>\d+)\s+duration_sec=(?P<duration>[\d.]+)\s+throughput_mps=(?P<throughput>[\d.]+)"
)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", default=str(RESULT_DIR), help="Path to results directory")
    parser.add_argument("--output-csv", default=str(OUT_CSV), help="Output CSV path")
    return parser.parse_args()


def parse_result_line(line: str) -> dict | None:
    match = RESULT_RE.search(line)
    if not match:
        return None
    return {
        "completed": int(match.group("completed")),
        "errors": int(match.group("errors")),
        "duration_sec": float(match.group("duration")),
        "throughput_mps": float(match.group("throughput")),
    }


def parse_sweep_dir(name: str) -> dict | None:
    match = NEW_SWEEP_DIR_RE.match(name)
    if not match:
        match = LEGACY_SWEEP_DIR_RE.match(name)
        if not match:
            return None
        return {
            "mode": "legacy",
            "tenant_count": int(match.group("tenants")),
            "attacker_rate": int(match.group("rate")),
            "timestamp": match.group("ts"),
        }

    mode = match.group("mode")
    if mode.startswith("sidecarless"):
        mode = "sidecarless"

    return {
        "mode": mode,
        "tenant_count": int(match.group("tenants")),
        "attacker_rate": int(match.group("rate")),
        "timestamp": match.group("ts"),
    }


def discover(results_dir: Path) -> list[dict]:
    rows: list[dict] = []

    for dirpath, dirnames, filenames in os.walk(results_dir):
        dirnames[:] = [d for d in dirnames if not d.startswith("archive_")]
        current_dir = Path(dirpath)
        if current_dir.name.startswith("archive_"):
            continue

        sweep = parse_sweep_dir(current_dir.name)
        if not sweep:
            continue

        for fname in filenames:
            if not (fname.startswith("worker_") and fname.endswith(".log")):
                continue
            worker = fname.replace("worker_", "").replace(".log", "")
            fpath = current_dir / fname
            try:
                for line in fpath.read_text().splitlines():
                    result = parse_result_line(line)
                    if not result:
                        continue
                    rows.append({
                        "timestamp": sweep["timestamp"],
                        "mode": sweep["mode"],
                        "tenant_count": sweep["tenant_count"],
                        "attacker_rate": sweep["attacker_rate"],
                        "worker": worker,
                        **result,
                    })
            except OSError as exc:
                print(f"Error reading {fpath}: {exc}", file=sys.stderr)

    return rows


def write_csv(rows: list[dict], output_csv: Path):
    fieldnames = [
        "timestamp",
        "mode",
        "tenant_count",
        "attacker_rate",
        "worker",
        "completed",
        "errors",
        "duration_sec",
        "throughput_mps",
    ]
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="") as csvf:
        writer = csv.DictWriter(csvf, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(rows, key=lambda r: (r["mode"], r["tenant_count"], r["attacker_rate"], r["worker"])):
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def main():
    args = parse_args()
    results_dir = Path(args.results_dir)
    output_csv = Path(args.output_csv)

    if not results_dir.exists():
        print(f"Result directory not found: {results_dir}")
        sys.exit(1)

    rows = discover(results_dir)
    if not rows:
        print(f"No RESULT lines found in {results_dir}", file=sys.stderr)
        sys.exit(1)

    write_csv(rows, output_csv)
    print(f"Wrote {len(rows)} rows to {output_csv}")


if __name__ == "__main__":
    main()
