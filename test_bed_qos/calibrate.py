#!/usr/bin/env python3
"""calibrate.py - derive the controllers' load constants from a measured sweep (Phase 5).

Input: a workload-10 sweep run (run_matrix.py --sweep, arm qos_open = same data plane, nothing limited):
victim latency measured while the attack rate rises in steps.

The node's "capacity" is the knee of that curve: the highest total tenant-ingress packet rate at which
the pooled victim P99 is still within `tolerance` of the no-attack P99 (linear interpolation between the
last level inside the tolerance and the first one outside). From it:

  capacity_pps    -> qos_controller_auto.py --capacity-pps (fair-share classifier: nobody is flagged
                     while total demand is below this; above it, tenants beyond their share are)
  h0_hits_per_sec -> knee H0 of the Hill cap r*(H) = rmax / (1 + (H/H0)^gamma) used by the reactive and
                     Markov controllers (H = hits/s from update_counter_map at the same operating point),
                     and the DTMC state thresholds (H0/3, H0, 2*H0)

Writes calibration/<tenants name>.json (read via calibration.py by arms.py and qos_controller.py;
override with QOS_CALIBRATION=path, or AUTO_CAPACITY_PPS for the capacity alone).

Usage: python3 calibrate.py workloads/10_scale/results/sweep_N3_scale100_<ts> [--tolerance 0.25]
"""
import argparse
import csv
import json
import os
import statistics
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_rows(run_dir):
    rows = []
    with open(os.path.join(run_dir, "results_summary_raw.csv")) as f:
        for r in csv.DictReader(f):
            if r["arch"] != "qos_open" or r["flood_ok"] != "True" or not r["p99_ms"]:
                continue
            rows.append({"scenario": r["scenario"], "p99": float(r["p99_ms"]), "ingress": float(r["ingress_pps"]),
                         "hits": float(r["hits_per_sec"]) if r["hits_per_sec"] else None})
    return rows


def knee(levels, tolerance):
    """levels: [(ingress_pps, p99_ms, hits)] sorted by ingress, levels[0] = no attack."""
    base = levels[0][1]
    limit = base * (1 + tolerance)
    last_ok = levels[0]
    for lv in levels[1:]:
        if lv[1] <= limit:
            last_ok = lv
            continue
        # interpolate between last_ok and lv where p99 crosses the limit
        (x0, y0, h0), (x1, y1, h1) = last_ok, lv
        f = 0.0 if y1 == y0 else max(0.0, min(1.0, (limit - y0) / (y1 - y0)))
        hx = None if h0 is None or h1 is None else h0 + f * (h1 - h0)
        return x0 + f * (x1 - x0), hx, base, limit, True
    return last_ok[0], last_ok[2], base, limit, False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--tolerance", type=float, default=0.25, help="allowed relative P99 increase (default 0.25)")
    ap.add_argument("--out", default=None, help="default: calibration/<tenants name of the sweep>.json")
    a = ap.parse_args()
    rows = load_rows(a.run_dir)
    if a.out is None:
        sys.path.insert(0, BASE_DIR)
        import tenants
        cfg = json.load(open(os.path.join(a.run_dir, "config.json")))
        a.out = os.path.join(BASE_DIR, "calibration", tenants.load(cfg["tenants_file"]).name + ".json")
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
    by = {}
    for r in rows:
        by.setdefault(r["scenario"], []).append(r)
    if "none" not in by:
        sys.exit("sweep has no valid 'none' (no attack) level")
    levels = []
    print(f"{'level':<10} {'n':>2} {'ingress pps':>12} {'hits/s':>10} {'P99 ms (median)':>16}")
    for sc, rs in by.items():
        ing = statistics.median(r["ingress"] for r in rs)
        p99 = statistics.median(r["p99"] for r in rs)
        hits = [r["hits"] for r in rs if r["hits"] is not None]
        h = statistics.median(hits) if hits else None
        levels.append((ing, p99, h, sc, len(rs)))
    levels.sort(key=lambda x: (x[3] != "none", x[0]))
    for ing, p99, h, sc, n in levels:
        print(f"{sc:<10} {n:>2} {ing:>12.0f} {h if h is not None else float('nan'):>10.0f} {p99:>16.3f}")
    cap, h0, base, limit, crossed = knee([(x[0], x[1], x[2]) for x in levels], a.tolerance)
    out = {
        "capacity_pps": round(cap),
        "h0_hits_per_sec": round(h0) if h0 else None,
        "baseline_p99_ms": round(base, 4),
        "p99_limit_ms": round(limit, 4),
        "tolerance": a.tolerance,
        "knee_found": crossed,
        "note": ("knee interpolated where pooled victim P99 crosses (1+tolerance) x no-attack P99" if crossed else
                 "P99 never left the tolerance band: capacity = highest measured ingress (node not saturated in range)"),
        "levels": [{"scenario": sc, "n": n, "ingress_pps": round(ing), "hits_per_sec": round(h) if h else None,
                    "p99_ms": round(p99, 4)} for ing, p99, h, sc, n in levels],
        "source": os.path.abspath(a.run_dir),
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    with open(a.out, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nbaseline P99 {base:.3f} ms, limit {limit:.3f} ms -> capacity_pps = {out['capacity_pps']}, "
          f"H0 = {out['h0_hits_per_sec']} hits/s ({'knee' if crossed else 'no knee in range'})\nwrote {a.out}")


if __name__ == "__main__":
    main()
