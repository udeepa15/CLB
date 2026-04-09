#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import struct
import subprocess
import time
from pathlib import Path

import yaml


def _set_nested(obj: dict, key_path: str, value) -> None:
    parts = key_path.split(".")
    cur = obj
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _lookup_map_raw(map_name: str, key_hex: list[str]) -> list[int] | None:
    cp = subprocess.run(
        ["bpftool", "-j", "map", "lookup", "name", map_name, "key", "hex", *key_hex],
        text=True,
        capture_output=True,
        check=False,
    )
    if cp.returncode != 0:
        return None
    try:
        payload = json.loads(cp.stdout.strip() or "{}")
    except Exception:
        return None
    vals = payload.get("value", [])
    try:
        return [int(v, 16) for v in vals]
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Track policy convergence lag from config change to eBPF map reflection")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--set-key", default="global.p99_threshold_ms")
    ap.add_argument("--set-value", required=True)
    ap.add_argument("--map-name", default="control_map")
    ap.add_argument("--expected-drop-rate", type=int, default=None)
    ap.add_argument("--apply-command", default="")
    ap.add_argument("--poll-interval-ms", type=int, default=20)
    ap.add_argument("--timeout-ms", type=int, default=10000)
    ap.add_argument("--report", default="results/raw/policy_convergence.csv")
    ap.add_argument("--experiment", default="policy_convergence")
    ap.add_argument("--tenant", default="control_plane")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    try:
        new_value = float(args.set_value)
    except Exception:
        new_value = args.set_value

    t0 = time.perf_counter()
    _set_nested(data, args.set_key, new_value)
    cfg_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    if args.apply_command.strip():
        subprocess.run(args.apply_command, shell=True, check=False)

    matched = False
    observed = None
    key0 = ["00", "00", "00", "00"]

    deadline = t0 + (args.timeout_ms / 1000.0)
    while time.perf_counter() < deadline:
        raw = _lookup_map_raw(args.map_name, key0)
        if raw:
            observed = raw
            if args.expected_drop_rate is None:
                matched = True
                break
            if len(raw) >= 4:
                drop = struct.unpack("<I", bytes(raw[:4]))[0]
                if drop == args.expected_drop_rate:
                    matched = True
                    break
        time.sleep(max(args.poll_interval_ms, 1) / 1000.0)

    convergence_ms = (time.perf_counter() - t0) * 1000.0

    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    existed = report.exists()
    with report.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if not existed:
            w.writerow(
                [
                    "timestamp",
                    "experiment",
                    "tenant",
                    "config_key",
                    "config_value",
                    "map_name",
                    "expected_drop_rate",
                    "convergence_ms",
                    "matched",
                ]
            )
        w.writerow(
            [
                time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                args.experiment,
                args.tenant,
                args.set_key,
                args.set_value,
                args.map_name,
                "" if args.expected_drop_rate is None else args.expected_drop_rate,
                f"{convergence_ms:.3f}",
                str(matched).lower(),
            ]
        )

    print(f"convergence_ms={convergence_ms:.3f}")
    print(f"matched={str(matched).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
