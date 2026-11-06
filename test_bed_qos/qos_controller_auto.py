#!/usr/bin/env python3
"""
qos_controller_auto.py - reactive rate controller with AUTOMATIC attacker/victim identification.

No victim or attacker address is configured anywhere. Each tick the controller:
  1. reads per-tenant packet counters from the kernel (ebpf_qos_auto.c),
  2. classifies heavy hitters by max-min fair share (fairshare.FairShareClassifier),
  3. caps ONLY the heavy hitters, using the same Hill function as qos_controller.py
     (so this arm differs from qos_dynamic_fixed only in WHO gets capped),
  4. releases a tenant (rate 0 = uncapped) once it stops exceeding its share.

--key ip    Stage A: identity = IPv4 source (spoofable)
--key port  Stage B: identity = tenant's ingress veth (ifindex), not spoofable within the node

Polling is fixed (default 0.2 s) to match qos_dynamic_fixed.

Usage: sudo python3 qos_controller_auto.py --key port --capacity-pps 10000 --log-file out.jsonl
"""
import argparse
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qos_controller as qc                      # reuse bpftool helpers + Hill function (parity with other arms)
from fairshare import FairShareClassifier

PIN = "/sys/fs/bpf/tc/globals/"
MAPS = {
    "ip": {"stats": PIN + "ip_stats", "limit": PIN + "percpu_rate_limit_map"},
    "port": {"stats": PIN + "port_stats", "limit": PIN + "port_rate_limit_map"},
}


# ---------------------------------------------------------------- bpftool output parsing
def _key_to_int(k):
    """bpftool prints a u32 key either as an int (BTF) or as a list of hex byte strings (raw).
    Both mean the same little-endian u32 as stored by the kernel."""
    if isinstance(k, int):
        return k
    if isinstance(k, list) and k:
        return int.from_bytes(bytes(int(x, 16) if isinstance(x, str) else int(x) for x in k[:4]), "little")
    raise ValueError(f"unrecognised key {k!r}")


def _packets_from_value(v):
    """Per-CPU value -> packet count. BTF: {"packets": n, "bytes": m}. Raw: 16 hex bytes (2 x u64 LE)."""
    if isinstance(v, dict):
        return int(v["packets"])
    if isinstance(v, list) and len(v) >= 8:
        return int.from_bytes(bytes(int(x, 16) if isinstance(x, str) else int(x) for x in v[:8]), "little")
    raise ValueError(f"unrecognised value {v!r}")


def parse_stats_dump(text):
    """`bpftool map dump -j` of a per-CPU stats map -> {key_int: total_packets_across_cpus}."""
    out = {}
    for e in json.loads(text or "[]"):
        f = e.get("formatted") or e
        key = _key_to_int(f.get("key", e.get("key")))
        vals = f.get("values", e.get("values", []))
        out[key] = out.get(key, 0) + sum(_packets_from_value(v["value"]) for v in vals)
    return out


def key_to_ip(k):
    return socket.inet_ntoa(struct.pack("<I", k))


def bridge_ports(bridge="br-mesh"):
    """{ifindex: name} of the bridge's member ports, from `ip -o link show master br-mesh`."""
    out = subprocess.run(["ip", "-o", "link", "show", "master", bridge], capture_output=True, text=True).stdout
    ports = {}
    for line in out.splitlines():
        m = re.match(r"\s*(\d+):\s+([^:@\s]+)", line)
        if m:
            ports[int(m.group(1))] = m.group(2)
    return ports


# ---------------------------------------------------------------- kernel I/O
def read_stats(key_mode):
    res = qc.run_bpftool(f"map dump pinned {MAPS[key_mode]['stats']} -j")
    if res.returncode != 0:
        return None
    return parse_stats_dump(res.stdout)


_WARNED = set()


def write_limit(key_mode, key_int, rate_bytes_per_sec, max_burst_bytes=10_000_000):
    """Write a per-CPU rate_limit_entry for either key space (ip / port ifindex).

    One 48-byte entry is replicated to all CPUs by bpftool v5.15 (one entry per CPU is rejected);
    fall back to one per possible CPU for builds that need it. Returns True on success; failures
    are warned once on stderr and counted by the caller (a silent failure here once hid that no
    cap was ever applied)."""
    u64 = lambda v: " ".join(str(b) for b in int(v).to_bytes(8, "little"))   # noqa: E731
    zero = "0 0 0 0 0 0 0 0"
    entry = f"{u64(rate_bytes_per_sec)} {u64(max_burst_bytes)} {u64(max_burst_bytes)} {zero} {zero} {zero}"
    key = " ".join(str(b) for b in int(key_int).to_bytes(4, "little"))
    pin = MAPS[key_mode]["limit"]
    res = qc.run_bpftool(f"map update pinned {pin} key {key} value {entry}")
    if res.returncode != 0:
        res = qc.run_bpftool(f"map update pinned {pin} key {key} value " + " ".join([entry] * qc._possible_cpus()))
    if res.returncode != 0:
        err = res.stderr.strip()[:200]
        if err not in _WARNED:
            _WARNED.add(err)
            print(f"[WARN] write_limit({key_mode}, {key_int}, {rate_bytes_per_sec}) FAILED: {err}", file=sys.stderr, flush=True)
    return res.returncode == 0


# ---------------------------------------------------------------- main loop
def main():
    ap = argparse.ArgumentParser(description="Reactive controller with automatic attacker identification")
    ap.add_argument("--key", choices=["ip", "port"], default="port")
    ap.add_argument("--capacity-pps", type=float, default=10_000.0,
                    help="unique ingress packets/s the node sustains without latency damage "
                         "(constant for now; Phase 5 will calibrate it automatically)")
    ap.add_argument("--interval", type=float, default=0.2)
    ap.add_argument("--margin", type=float, default=0.25)
    ap.add_argument("--k-on", type=int, default=2)
    ap.add_argument("--m-off", type=int, default=5)
    ap.add_argument("--alpha", type=float, default=0.5)
    ap.add_argument("--log-file", default="qos_controller_auto_log.jsonl")
    args = ap.parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(args.log_file)), exist_ok=True)

    cls = FairShareClassifier(args.capacity_pps, margin=args.margin, k_on=args.k_on,
                              m_off=args.m_off, alpha=args.alpha)
    ports = bridge_ports()
    label = (lambda k: ports.get(k, f"if{k}")) if args.key == "port" else key_to_ip
    print(f"[{datetime.now().strftime('%H:%M:%S')}] auto controller key={args.key} capacity={args.capacity_pps}pps "
          f"interval={args.interval}s ports={ports} log={args.log_file}", flush=True)

    capped = set()
    last_time, last_hits = time.time(), qc.read_update_counter()
    with open(args.log_file, "a") as log_f:
        while True:
            time.sleep(args.interval)
            now = time.time()
            dt = now - last_time
            if dt <= 0:
                continue
            counts = read_stats(args.key)
            if counts is None:               # maps not ready yet; skip this tick
                last_time = now
                continue
            hits = qc.read_update_counter()
            hits_per_sec = (hits - last_hits) / dt
            last_time, last_hits = now, hits

            d = cls.update(counts, dt)
            # same Hill cap as the other reactive arms ("heavy" is deliberately not in VICTIM_IPS)
            cap = qc.solve_stackelberg_rates(hits_per_sec, 0, "heavy")
            write_fail = 0
            for k in d.heavy:                # refresh each tick: the cap tracks contention
                write_fail += 0 if write_limit(args.key, k, cap) else 1
                capped.add(k)
            for k in d.released:
                write_fail += 0 if write_limit(args.key, k, 0) else 1  # rate 0 = uncapped
                capped.discard(k)

            top = sorted(d.rates.items(), key=lambda kv: -kv[1])[:8]
            log_f.write(json.dumps({
                "timestamp": now, "dt_sec": round(dt, 4), "key_mode": args.key,
                "hits_per_sec": round(hits_per_sec, 2), "total_pps": round(d.total_pps, 1),
                "share_pps": None if d.share == float("inf") else round(d.share, 1),
                "heavy": sorted(label(k) for k in d.heavy),
                "newly_heavy": sorted(label(k) for k in d.newly_heavy),
                "released": sorted(label(k) for k in d.released),
                "n_sources": len(d.rates),
                "top_rates_pps": {label(k): round(r, 1) for k, r in top},
                "heavy_cap_bps": cap if d.heavy else None,
                "write_failures": write_fail,
            }) + "\n")
            log_f.flush()


if __name__ == "__main__":
    main()
