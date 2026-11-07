#!/usr/bin/env python3
"""run_density.py - container-density test: the same matrix at N = 1, 10, 20 ... 100 victim containers.

For every level N (in a seeded random order, so time-of-day or thermal drift cannot line up with N) it:
  1. generates tenants/density/v<N>.json: N victim servers, max(1, 5% of N) attackers, and for N = 1 one
     client-only tenant (a container calling its own IP never leaves its namespace),
  2. runs workload 10 (run_matrix.py) on it into <out>/N<N>/ - topology and containers built once per level,
  3. records the outcome in <out>/density_status.json.

Every level uses ONE calibration (--calibration, default the N=100 one): whether it transfers across
densities is part of the test (false positives in the 'none' scenario). Load per victim client is fixed
(--qps), so total load grows linearly with N. The measurement window is --duration, lengthened for small N
so every trial has at least --min-requests requests (capped at --max-duration).

Resumable: levels whose run finished (rc 0) are skipped when the same --out is given again.

  sudo python3 run_density.py --reps 5                              # config A (shared CPUs)
  sudo python3 run_density.py --reps 5 --tenant-cpus 1-5 --attacker-cpus 6-7 --rps \\
       --calibration ../../calibration/scale100_rps.json            # config B
  sudo python3 run_density.py --out results/density_<ts>            # resume
"""
import argparse
import json
import math
import os
import random
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, BASE_DIR)
import tenants  # noqa: E402

DEFAULT_LEVELS = [1] + list(range(10, 101, 10))
DEFAULT_ARMS = ["sidecarless", "qos_open", "qos_static", "qos_dynamic_fixed", "qos_auto_port"]
DEFAULT_SCENARIOS = ["none", "a1_flood", "a5pct_flood"]


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def level_topology(n, tenant_cpus):
    n_att = max(1, n * 5 // 100)
    topo = tenants.generate(n, n_att, f"density_v{n}", tenant_cpus, n_clients=1 if n == 1 else 0)
    path = os.path.join(BASE_DIR, "tenants", "density", f"v{n}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(topo.to_json() + "\n")
    return topo, path


def level_duration(topo, a):
    """Window so that every trial has >= --min-requests requests (same pairing rule as run_matrix.ring_pairs:
    servers call each other when there are >= 2; client-only tenants always call)."""
    servers = topo.servers()
    pairs = (len(servers) if len(servers) > 1 else 0) + sum(1 for t in topo.clients() if not t.server)
    need = math.ceil(a.min_requests / (a.qps * max(1, pairs)))
    return int(min(a.max_duration, max(a.duration, need)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--levels", type=int, nargs="+", default=DEFAULT_LEVELS)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--architectures", nargs="+", default=DEFAULT_ARMS)
    ap.add_argument("--scenarios", nargs="+", default=DEFAULT_SCENARIOS)
    ap.add_argument("--duration", type=int, default=30)
    ap.add_argument("--min-requests", type=int, default=1200, help="per trial; small N get a longer window")
    ap.add_argument("--max-duration", type=int, default=60)
    ap.add_argument("--qps", type=float, default=20)
    ap.add_argument("--calibration", default=os.path.join(BASE_DIR, "calibration", "scale100.json"))
    ap.add_argument("--tenant-cpus", default="1-7")
    ap.add_argument("--attacker-cpus", default=None)
    ap.add_argument("--rps", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=None, help="results dir; an existing one is resumed")
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("must run as root")
    if not os.path.exists(a.calibration):
        sys.exit(f"calibration file {a.calibration} not found (run the calibration sweep first)")

    out = os.path.abspath(a.out or os.path.join(HERE, "results", f"density_{datetime.now().strftime('%Y%m%d_%H%M%S')}"))
    os.makedirs(out, exist_ok=True)
    status_path = os.path.join(out, "density_status.json")
    status = json.load(open(status_path)) if os.path.exists(status_path) else {}
    cfg_path = os.path.join(out, "density_config.json")
    if os.path.exists(cfg_path):
        order = json.load(open(cfg_path))["level_order"]
        log(f"resuming {out}")
    else:
        order = list(a.levels)
        random.Random(a.seed).shuffle(order)
        with open(cfg_path, "w") as f:
            json.dump({**vars(a), "out": out, "level_order": order,
                       "calibration_used": json.load(open(a.calibration))}, f, indent=2)

    passthru = ["--tenant-cpus", a.tenant_cpus] + (["--attacker-cpus", a.attacker_cpus] if a.attacker_cpus else []) \
        + (["--rps"] if a.rps else [])
    log(f"density test: levels {order}, {len(a.architectures)} arms x {len(a.scenarios)} scenarios x {a.reps} reps per level")
    for n in order:
        key = str(n)
        if status.get(key, {}).get("rc") == 0:
            log(f"N={n}: done earlier, skipping")
            continue
        topo, tfile = level_topology(n, a.tenant_cpus)
        dur = level_duration(topo, a)
        level_dir = os.path.join(out, f"N{n}")
        if os.path.exists(level_dir):                      # partial run from an interruption: keep it aside
            os.rename(level_dir, f"{level_dir}_partial_{datetime.now().strftime('%H%M%S')}")
        cmd = ["python3", os.path.join(HERE, "run_matrix.py"), "--reps", str(a.reps),
               "--architectures", *a.architectures, "--scenarios", *a.scenarios,
               "--duration", str(dur), "--qps", str(a.qps), "--out", level_dir, *passthru]
        env = {**os.environ, "TENANTS_FILE": tfile, "QOS_CALIBRATION": os.path.abspath(a.calibration),
               "PYTHONDONTWRITEBYTECODE": "1"}
        log(f"N={n}: {len(topo.servers())} victim containers, {len(topo.attackers())} attackers, window {dur}s")
        t0 = time.time()
        rc = subprocess.run(cmd, env=env, cwd=HERE).returncode
        rows = 0
        csv = os.path.join(level_dir, "results_summary_raw.csv")
        if os.path.exists(csv):
            rows = sum(1 for _ in open(csv)) - 1
        status[key] = {"rc": rc, "rows": rows, "expected": len(a.architectures) * len(a.scenarios) * a.reps,
                       "duration_s": dur, "minutes": round((time.time() - t0) / 60, 1),
                       "finished": datetime.now().isoformat(timespec="seconds")}
        with open(status_path, "w") as f:
            json.dump(status, f, indent=2)
        log(f"N={n}: rc={rc}, {rows}/{status[key]['expected']} trials, {status[key]['minutes']} min")
    log(f"density test complete: {out}")


if __name__ == "__main__":
    main()
