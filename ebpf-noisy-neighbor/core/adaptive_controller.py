#!/usr/bin/env python3
"""Latency-aware adaptive controller for tc/eBPF throttling."""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import time
from collections import deque
from pathlib import Path


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def read_recent_p99(latency_file: Path, window_size: int) -> float | None:
    vals: deque[float] = deque(maxlen=window_size)
    if not latency_file.exists():
        return None

    with latency_file.open("r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.strip().rsplit(",", 1)
            if len(parts) != 2:
                continue
            try:
                vals.append(float(parts[1]))
            except ValueError:
                continue

    if not vals:
        return None

    arr = sorted(vals)
    idx = int(round(0.99 * (len(arr) - 1)))
    return arr[max(0, min(idx, len(arr) - 1))]


def read_noisy_cgroup_id(runtime_dir: Path) -> int | None:
    p = runtime_dir / "cgroup_ids.csv"
    if not p.exists():
        return None

    with p.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("name") == "noisy":
                try:
                    return int(row["cgroup_id"])
                except Exception:
                    return None
    return None


def set_global(root_dir: Path, drop_rate: int, identity_mode: str, noisy_ip: str) -> None:
    _run(
        [
            "python3",
            str(root_dir / "core" / "bpf_map_ctl.py"),
            "set-global",
            "--drop-rate",
            str(drop_rate),
            "--identity-mode",
            identity_mode,
            "--noisy-ip",
            noisy_ip,
        ]
    )


def set_cgroup(root_dir: Path, cgroup_id: int, drop_rate: int) -> None:
    _run(
        [
            "python3",
            str(root_dir / "core" / "bpf_map_ctl.py"),
            "set-cgroup",
            "--cgroup-id",
            str(cgroup_id),
            "--drop-rate",
            str(drop_rate),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Adaptive eBPF latency controller")
    parser.add_argument("--root", required=True)
    parser.add_argument("--latency-file", required=True)
    parser.add_argument("--p99-threshold-ms", type=float, required=True)
    parser.add_argument("--identity-mode", choices=["ip", "cgroup"], default="ip")
    parser.add_argument("--noisy-ip", default="10.0.0.4")
    parser.add_argument("--sample-interval", type=float, default=1.0)
    parser.add_argument("--window-size", type=int, default=120)
    parser.add_argument("--step-up", type=int, default=100)
    parser.add_argument("--step-down", type=int, default=50)
    parser.add_argument("--min-rate", type=int, default=0)
    parser.add_argument("--max-rate", type=int, default=950)
    parser.add_argument("--log-file", required=True)
    args = parser.parse_args()

    root_dir = Path(args.root)
    latency_file = Path(args.latency_file)
    log_file = Path(args.log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    stop_flag = root_dir / "results" / "raw" / ".adaptive_stop"
    if stop_flag.exists():
        stop_flag.unlink()

    current_rate = max(args.min_rate, min(args.max_rate, 250))
    set_global(root_dir, current_rate, args.identity_mode, args.noisy_ip)

    cgroup_id = read_noisy_cgroup_id(root_dir / "containers" / "runtime")
    if args.identity_mode == "cgroup" and cgroup_id is not None:
        set_cgroup(root_dir, cgroup_id, current_rate)

    with log_file.open("a", encoding="utf-8") as log:
        log.write("timestamp,event,p99_ms,drop_rate_per_mille,identity_mode\n")

    while not stop_flag.exists():
        p99 = read_recent_p99(latency_file, args.window_size)

        if p99 is not None:
            if p99 > args.p99_threshold_ms:
                current_rate = min(args.max_rate, current_rate + args.step_up)
            else:
                current_rate = max(args.min_rate, current_rate - args.step_down)

            set_global(root_dir, current_rate, args.identity_mode, args.noisy_ip)
            if args.identity_mode == "cgroup" and cgroup_id is not None:
                set_cgroup(root_dir, cgroup_id, current_rate)

            with log_file.open("a", encoding="utf-8") as log:
                log.write(
                    f"{time.time():.3f},control_update,{p99:.3f},{current_rate},{args.identity_mode}\n"
                )

        time.sleep(max(args.sample_interval, 0.1))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
