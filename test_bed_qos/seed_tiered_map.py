#!/usr/bin/env python3
"""Seed tenant_qos_map for the qos_tiered arm with the exact struct layout.

struct tenant_qos_state (ebpf_qos_tiered.c), 56 bytes with natural alignment:
    u32 tier; [4 B padding]; u64 tokens, last_update_ns, rate_bytes_per_sec,
    u64 max_burst_bytes, packets_passed, packets_dropped

Unlike the old inline bpftool calls this fails loudly (no `|| true`) and
verifies the entries by reading them back.

Usage: sudo python3 seed_tiered_map.py [--l2-rate-bps N] [--burst-bytes N]
"""
import argparse
import json
import socket
import struct
import subprocess
import sys

MAP_PIN = "/sys/fs/bpf/tc/globals/tenant_qos_map"
BPFTOOL = "/usr/sbin/bpftool"
STRUCT = struct.Struct("<I4xQQQQQQ")  # 56 bytes
assert STRUCT.size == 56

VICTIMS = ["10.0.0.10", "10.0.0.11", "10.0.0.12"]  # L0, uncapped
ATTACKER = "10.0.0.20"                             # L2, capped


def bpftool(*args):
    return subprocess.run([BPFTOOL, *args], check=True, capture_output=True, text=True).stdout


def update(ip, tier, rate, burst):
    # tokens start full so the first packets are not dropped before the first refill
    value = STRUCT.pack(tier, burst, 0, rate, burst, 0, 0)
    key = [str(b) for b in socket.inet_aton(ip)]
    bpftool("map", "update", "pinned", MAP_PIN, "key", *key,
            "value", *[str(b) for b in value])


def verify(ip, tier, rate):
    """Read the entry back. bpftool prints raw hex bytes without BTF, or a 'formatted'
    field dict with BTF (our maps are BTF-defined), so accept both."""
    key = [str(b) for b in socket.inet_aton(ip)]
    out = bpftool("map", "lookup", "pinned", MAP_PIN, "key", *key, "-j")
    j = json.loads(out)
    if isinstance(j.get("formatted", {}).get("value"), dict):
        v = j["formatted"]["value"]
        got_tier, got_rate = int(v["tier"]), int(v["rate_bytes_per_sec"])
    elif isinstance(j.get("value"), list) and len(j["value"]) == STRUCT.size:
        raw = bytes(int(x, 16) for x in j["value"])
        got_tier, _, _, got_rate, *_ = STRUCT.unpack(raw)
    else:
        sys.exit(f"verify failed for {ip}: unrecognised bpftool output {out!r}")
    if got_tier != tier or got_rate != rate:
        sys.exit(f"verify failed for {ip}: tier={got_tier} rate={got_rate}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--l2-rate-bps", type=int, default=5_000_000,
                    help="attacker (L2) cap in bytes/s (default 5 MB/s)")
    ap.add_argument("--burst-bytes", type=int, default=0,
                    help="attacker burst; default = max(rate/10, 65536)")
    a = ap.parse_args()
    if a.l2_rate_bps <= 0:
        sys.exit("--l2-rate-bps must be > 0 (rate 0 never refills tokens => 100% drop)")
    burst = a.burst_bytes or max(a.l2_rate_bps // 10, 65536)

    for ip in VICTIMS:
        update(ip, 0, 0, 0)
        verify(ip, 0, 0)
    update(ATTACKER, 2, a.l2_rate_bps, burst)
    verify(ATTACKER, 2, a.l2_rate_bps)
    print(f"tenant_qos_map seeded: victims L0 uncapped, {ATTACKER} L2 "
          f"rate={a.l2_rate_bps} B/s burst={burst} B")


if __name__ == "__main__":
    main()
