#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent / "ebpf_research"
DEFAULT_INPUT_DIR = ROOT / "results" / "raw"
DEFAULT_OUTPUT = ROOT / "results" / "cleaned_matrix_metrics.csv"

MODES = ["baseline", "isolated", "shared"]

VICTIM_LOG_RE = re.compile(
    r"^wrk2_v(?P<victim_id>\d+)_of_(?P<victim_count>\d+)_(?P<mode>baseline|isolated|shared)_(?P<attacker_rate>\d+)_(?P<ts>\d+)\.log$"
)
ATTACKER_LOG_RE = re.compile(
    r"^wrk2_adv_(?P<mode>baseline|isolated|shared)_v?(?P<victim_count>\d+)_r(?P<attacker_rate>\d+)_(?P<ts>\d+)\.log$"
)
RESULT_RE = re.compile(
    r"RESULT\s+target_qps=(?P<target_qps>\S+)\s+actual_qps=(?P<actual_qps>\S+)\s+"
    r"p50_us=(?P<p50_us>\d+)\s+p95_us=(?P<p95_us>\d+)\s+p99_us=(?P<p99_us>\d+)\s+p999_us=(?P<p999_us>\d+)"
)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse and clean multi-tenant wrk2 benchmarks.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR, help="Directory containing raw log files")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output destination for summarized CSV")
    return parser.parse_args()

def extract_metrics_from_log(file_path: Path) -> dict | None:
    try:
        content = file_path.read_text()
        result_match = RESULT_RE.search(content)
        if result_match:
            return {
                "p50_us": float(result_match.group("p50_us")),
                "p95_us": float(result_match.group("p95_us")),
                "p99_us": float(result_match.group("p99_us")),
                "p999_us": float(result_match.group("p999_us")),
            }

        p50 = re.search(r"^p50:\s+([\d\.]+)(us|ms)$", content, re.MULTILINE)
        p95 = re.search(r"^p95:\s+([\d\.]+)(us|ms)$", content, re.MULTILINE)
        p99 = re.search(r"^p99:\s+([\d\.]+)(us|ms)$", content, re.MULTILINE)
        p999 = re.search(r"^p99\.9:\s+([\d\.]+)(us|ms)$", content, re.MULTILINE)

        if not (p50 and p95 and p99 and p999):
            return None

        def to_us(match) -> float:
            value, unit = float(match.group(1)), match.group(2)
            return value if unit == "us" else value * 1000.0

        return {
            "p50_us": to_us(p50),
            "p95_us": to_us(p95),
            "p99_us": to_us(p99),
            "p999_us": to_us(p999),
        }
    except Exception:
        return None

def process_raw_logs(input_dir: Path) -> pd.DataFrame:
    records = []
    for file in input_dir.glob("wrk2_*.log"):
        victim_match = VICTIM_LOG_RE.match(file.name)
        attacker_match = ATTACKER_LOG_RE.match(file.name)

        victim_id = 0  # 0 explicitly flags Attacker/Adversary logs
        if victim_match:
            mode = victim_match.group("mode")
            victim_id = int(victim_match.group("victim_id"))
            victim_count = int(victim_match.group("victim_count"))
            attacker_rate = int(victim_match.group("attacker_rate"))
        elif attacker_match:
            mode = attacker_match.group("mode")
            victim_count = int(attacker_match.group("victim_count"))
            attacker_rate = int(attacker_match.group("attacker_rate"))
        else:
            continue

        if mode not in MODES:
            continue
            
        metrics = extract_metrics_from_log(file)
        if metrics:
            metrics.update({
                "mode": mode,
                "victim_count": victim_count,
                "attacker_rate": attacker_rate,
                "victim_id": victim_id
            })
            records.append(metrics)
            
    return pd.DataFrame(records)

def main() -> int:
    args = parse_args()
    if not args.input_dir.exists():
        print(f"Input directory does not exist: {args.input_dir}")
        return 1
        
    print(f"Scanning raw logs in {args.input_dir}...")
    df = process_raw_logs(args.input_dir)
    if df.empty:
        print("No valid log records extracted. Check your file naming convention!")
        return 1
        
    # FIX 1: Isolate real victims. Drop attacker data (victim_id == 0) from victim profile matrix.
    df = df[df["victim_id"] > 0]
    
    # FIX 2: Relax outlier filter. Only eliminate actual system deadlocks (>500ms).
    # This ensures valid high-load queueing stalls (e.g. 13.4ms) aren't deleted.
    df = df[df["p99_us"] < 500000]
    
    # Aggregation
    summary = (
        df.groupby(["mode", "victim_count", "attacker_rate"], as_index=False)
        .agg(
            sample_count=("victim_id", "count"),
            p50_ms=("p50_us", lambda x: x.median() / 1000.0),
            p95_ms=("p95_us", lambda x: x.median() / 1000.0),
            p99_ms=("p99_us", lambda x: x.median() / 1000.0),
            p999_ms=("p999_us", lambda x: x.median() / 1000.0),
        )
        .sort_values(["victim_count", "mode", "attacker_rate"])
        .reset_index(drop=True)
    )
    
    # Validation alert for low sample sizes
    low_samples = summary[summary["sample_count"] < 3]
    if not low_samples.empty:
        print("⚠️ WARNING: Some configurations have less than 3 samples. Latency plots may be noisy.")
    
    args.output.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(args.output, index=False)
    print(f"Wrote clean multidimensional matrix summary to {args.output}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())