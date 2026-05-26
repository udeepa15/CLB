#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
DEFAULT_INPUT_DIR = ROOT / "results" / "raw"
DEFAULT_OUTPUT = ROOT / "results" / "cleaned_matrix_metrics.csv"

MODES = ["baseline", "isolated", "shared"]
VICTIM_RE = re.compile(
    r"^wrk2_v(?P<victim_id>\d+)_of_(?P<victim_count>\d+)_(?P<mode>baseline|isolated|shared)_(?P<attacker_rate>\d+)_(?P<ts>\d+)\.log$"
)
ATTACKER_RE = re.compile(
    r"^wrk2_adv_(?P<mode>baseline|isolated|shared)_v(?P<victim_count>\d+)_r(?P<attacker_rate>\d+)_(?P<ts>\d+)\.log$"
)
RESULT_RE = re.compile(
    r"RESULT\s+target_qps=(?P<target_qps>\S+)\s+actual_qps=(?P<actual_qps>\S+)\s+"
    r"p50_us=(?P<p50_us>\d+)\s+p95_us=(?P<p95_us>\d+)\s+p99_us=(?P<p99_us>\d+)\s+p999_us=(?P<p999_us>\d+)"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize tenant isolation benchmark logs.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def extract_metrics(file_path: Path) -> dict | None:
    try:
        content = file_path.read_text()
    except OSError:
        return None

    match = RESULT_RE.search(content)
    if match:
        return {
            "target_qps": float(match.group("target_qps")) if match.group("target_qps") != "rate" else None,
            "actual_qps": float(match.group("actual_qps")) if match.group("actual_qps") != "rate" else None,
            "p50_us": float(match.group("p50_us")),
            "p95_us": float(match.group("p95_us")),
            "p99_us": float(match.group("p99_us")),
            "p999_us": float(match.group("p999_us")),
        }

    return None


def parse_log_name(name: str) -> dict | None:
    victim = VICTIM_RE.match(name)
    if victim:
        return {
            "role": f"v{victim.group('victim_id')}",
            "mode": victim.group("mode"),
            "victim_count": int(victim.group("victim_count")),
            "attacker_rate": int(victim.group("attacker_rate")),
            "timestamp": int(victim.group("ts")),
        }

    attacker = ATTACKER_RE.match(name)
    if attacker:
        return {
            "role": "adv",
            "mode": attacker.group("mode"),
            "victim_count": int(attacker.group("victim_count")),
            "attacker_rate": int(attacker.group("attacker_rate")),
            "timestamp": int(attacker.group("ts")),
        }

    return None


def load_rows(input_dir: Path) -> pd.DataFrame:
    rows: list[dict] = []
    for file_path in sorted(input_dir.glob("wrk2_*.log")):
        parsed = parse_log_name(file_path.name)
        if parsed is None or parsed["mode"] not in MODES:
            continue
        metrics = extract_metrics(file_path)
        if metrics is None:
            continue
        row = {**parsed, **metrics, "path": str(file_path)}
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> int:
    args = parse_args()
    if not args.input_dir.exists():
        print(f"Input directory does not exist: {args.input_dir}")
        return 1

    df = load_rows(args.input_dir)
    if df.empty:
        print(f"No wrk2 result rows found in {args.input_dir}")
        return 1

    victim_df = df[df["role"] != "adv"].copy()
    if victim_df.empty:
        print("No victim rows found to summarize")
        return 1

    summary = (
        victim_df.groupby(["mode", "victim_count", "attacker_rate"], as_index=False)
        .agg(
            sample_count=("role", "count"),
            p50_us=("p50_us", "median"),
            p95_us=("p95_us", "median"),
            p99_us=("p99_us", "median"),
            p999_us=("p999_us", "median"),
            target_qps=("target_qps", "median"),
            actual_qps=("actual_qps", "median"),
        )
        .sort_values(["victim_count", "mode", "attacker_rate"])
        .reset_index(drop=True)
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(args.output, index=False)
    print(f"Wrote summary to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
