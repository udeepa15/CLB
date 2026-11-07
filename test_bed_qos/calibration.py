#!/usr/bin/env python3
"""calibration.py - the load constants of the current tenants file (written by calibrate.py).

    capacity_pps     : fair-share capacity of the automatic arms (qos_controller_auto.py --capacity-pps)
    h0_hits_per_sec  : knee H0 of the Hill cap (reactive + Markov controllers) and of the DTMC states

Lookup: $QOS_CALIBRATION if set, else calibration/<tenants name>.json. Without a file the original
hand-picked constants are used (10000 pps, 15000 hits/s), so the default 4-tenant topology behaves as
before calibration existed.
"""
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import tenants  # noqa: E402

DEFAULTS = {"capacity_pps": 10000, "h0_hits_per_sec": 15000}


def path(topo_name=None):
    return os.environ.get("QOS_CALIBRATION") or os.path.join(BASE_DIR, "calibration",
                                                             f"{topo_name or tenants.load().name}.json")


def load(topo_name=None):
    p = path(topo_name)
    d = dict(DEFAULTS, source="defaults (hand-picked, uncalibrated)")
    if os.path.exists(p):
        with open(p) as f:
            d.update({k: v for k, v in json.load(f).items() if v is not None})
        d["source"] = p
    return d


if __name__ == "__main__":
    print(json.dumps(load(), indent=2))
