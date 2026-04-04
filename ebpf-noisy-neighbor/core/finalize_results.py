#!/usr/bin/env python3
"""Aggregate experiment results into publication-ready summaries."""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def to_float(v: str, default: float = float("nan")) -> float:
    try:
        return float(v)
    except Exception:
        return default


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def stddev(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def ci95(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    s = stddev(xs)
    return 1.96 * s / math.sqrt(len(xs))


def load_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_summary(rows: list[dict[str, str]], out_csv: Path) -> None:
    group_vals: dict[tuple[str, ...], list[float]] = defaultdict(list)

    for r in rows:
        key = (
            r.get("noise_level", ""),
            r.get("traffic_pattern", ""),
            r.get("isolation_method", ""),
            r.get("identity_mode", ""),
            r.get("containers", ""),
            r.get("tenant_cpu_quota_pct", ""),
            r.get("tenant_memory_mb", ""),
            r.get("tenant_cpuset", ""),
            r.get("noisy_cpu_quota_pct", ""),
            r.get("noisy_memory_mb", ""),
            r.get("noisy_cpuset", ""),
            r.get("host_reserved_cpus", ""),
            r.get("host_mem_pressure_mb", ""),
            r.get("io_read_bps", ""),
            r.get("io_write_bps", ""),
        )
        p99 = to_float(r.get("p99_ms", "nan"))
        if math.isfinite(p99):
            group_vals[key].append(p99)

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "noise_level",
            "traffic_pattern",
            "isolation_method",
            "identity_mode",
            "containers",
            "tenant_cpu_quota_pct",
            "tenant_memory_mb",
            "tenant_cpuset",
            "noisy_cpu_quota_pct",
            "noisy_memory_mb",
            "noisy_cpuset",
            "host_reserved_cpus",
            "host_mem_pressure_mb",
            "io_read_bps",
            "io_write_bps",
            "runs",
            "avg_p99_ms",
            "stddev_p99_ms",
            "ci95_p99_ms",
        ]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()

        for key in sorted(group_vals.keys()):
            vals = group_vals[key]
            w.writerow(
                {
                    "noise_level": key[0],
                    "traffic_pattern": key[1],
                    "isolation_method": key[2],
                    "identity_mode": key[3],
                    "containers": key[4],
                    "tenant_cpu_quota_pct": key[5],
                    "tenant_memory_mb": key[6],
                    "tenant_cpuset": key[7],
                    "noisy_cpu_quota_pct": key[8],
                    "noisy_memory_mb": key[9],
                    "noisy_cpuset": key[10],
                    "host_reserved_cpus": key[11],
                    "host_mem_pressure_mb": key[12],
                    "io_read_bps": key[13],
                    "io_write_bps": key[14],
                    "runs": len(vals),
                    "avg_p99_ms": f"{mean(vals):.4f}",
                    "stddev_p99_ms": f"{stddev(vals):.4f}",
                    "ci95_p99_ms": f"{ci95(vals):.4f}",
                }
            )


def copy_graphs(processed_dir: Path, graph_dir: Path) -> None:
    graph_dir.mkdir(parents=True, exist_ok=True)
    for p in processed_dir.glob("*.png"):
        shutil.copy2(p, graph_dir / p.name)


def write_metadata(root: Path, config_used: Path, metadata_path: Path) -> None:
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "config_path": str(config_used),
        "git_commit": "unknown",
    }

    head = root / ".git" / "HEAD"
    try:
        if head.exists():
            txt = head.read_text(encoding="utf-8").strip()
            if txt.startswith("ref:"):
                ref = txt.split(" ", 1)[1]
                ref_file = root / ".git" / ref
                if ref_file.exists():
                    payload["git_commit"] = ref_file.read_text(encoding="utf-8").strip()
            else:
                payload["git_commit"] = txt
    except Exception:
        pass

    metadata_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--config", required=True)
    args = ap.parse_args()

    root = Path(args.root)
    processed = root / "results" / "processed"
    final_dir = root / "results" / "final"

    rows = load_rows(processed / "results.csv")
    write_summary(rows, final_dir / "summary.csv")

    shutil.copy2(processed / "results.csv", final_dir / "results.csv") if (processed / "results.csv").exists() else None
    shutil.copy2(processed / "latency_distribution.csv", final_dir / "latency_distribution.csv") if (processed / "latency_distribution.csv").exists() else None

    copy_graphs(processed, final_dir / "graphs")
    write_metadata(root, Path(args.config), final_dir / "metadata.json")

    # Keep log snapshots for paper appendix/repro packages.
    raw_dir = root / "results" / "raw"
    logs_dst = final_dir / "logs"
    logs_dst.mkdir(parents=True, exist_ok=True)
    for p in raw_dir.glob("*.log"):
        shutil.copy2(p, logs_dst / p.name)

    print(f"[finalize] Wrote publication outputs under: {final_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
