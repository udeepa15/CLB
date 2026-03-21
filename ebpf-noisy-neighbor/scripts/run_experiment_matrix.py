#!/usr/bin/env python3
import sys
from pathlib import Path

import yaml


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: run_experiment_matrix.py <config.yaml>", file=sys.stderr)
        return 1

    cfg_path = Path(sys.argv[1])
    if not cfg_path.exists():
        print(f"missing config file: {cfg_path}", file=sys.stderr)
        return 1

    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    experiments = data.get("experiments", [])
    if not experiments:
        print("config has no experiments[] entries", file=sys.stderr)
        return 1

    for exp in experiments:
        name = str(exp["name"])
        noise = str(exp.get("noise_level", "medium"))
        ebpf = str(exp.get("ebpf", False)).lower()
        containers = int(exp.get("containers", 3))
        strategy = str(exp.get("strategy", "none"))
        requests = int(exp.get("requests", 300))

        # Pipe-separated stable format for bash reader.
        print(f"{name}|{noise}|{ebpf}|{containers}|{strategy}|{requests}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
