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

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse and clean multi-tenant wrk2 benchmarks.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR, help="Directory containing raw log files")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output destination for summarized CSV")
    return parser.parse_args()

def extract_metrics_from_log(file_path: Path) -> dict | None:
    """Parses custom latency reporter logs."""
    try:
        content = file_path.read_text()
        p50 = re.search(r"50\.000%\s+([\d\.]+)(us|ms)", content)
        p95 = re.search(r"95\.000%\s+([\d\.]+)(us|ms)", content)
        p99 = re.search(r"99\.000%\s+([\d\.]+)(us|ms)", content)
        p999 = re.search(r"99\.900%\s+([\d\.]+)(us|ms)", content)
        
        if not (p50 and p95 and p99 and p999):
            return None
            
        def to_us(match) -> float:
            val, unit = float(match.group(1)), match.group(2)
            return val if unit == "us" else val * 1000.0

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
    # Search for all files starting with wrk2_v, which covers your victim logs
    for file in input_dir.glob("wrk2_v*.log"):
        # Example filename: wrk2_v1_of_5_shared_35000_1779420779.log
        parts = file.stem.split('_')
        
        # We need at least 6 parts: wrk2, v{i}, of, {count}, {mode}, {rate}, {ts}
        if len(parts) >= 6 and "of" in parts:
            try:
                # parts[1] is 'v{id}', parts[3] is 'count', parts[4] is 'mode', parts[5] is 'rate'
                victim_id = int(parts[1].replace('v', ''))
                victim_count = int(parts[3])
                mode = parts[4]
                attacker_rate = int(parts[5])
            except (ValueError, IndexError):
                continue
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
        print("No valid victim log records extracted. Check your file naming convention!")
        return 1
        
    # Data cleaning: remove infrastructure outliers
    df = df[~(
        ((df["attacker_rate"] == 0) & (df["p99_us"] > 5000)) |
        ((df["attacker_rate"] > 0) & (df["p99_us"] > 13000))
    )]
    
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
    
    args.output.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(args.output, index=False)
    print(f"Wrote clean multidimensional matrix summary to {args.output}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())