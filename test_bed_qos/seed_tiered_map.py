#!/usr/bin/env python3
"""Seed tenant_qos_map for the qos_tiered arm with the exact struct layout.

struct tenant_qos_state (ebpf_qos_tiered.c), 56 bytes with natural alignment:
    u32 tier; [4 B padding]; u64 tokens, last_update_ns, rate_bytes_per_sec,
    u64 max_burst_bytes, packets_passed, packets_dropped

Fails loudly and verifies every entry by reading it back. Tenants and roles come from the
tenants file (tenants.py): victims are L0 (uncapped), attackers L2 (capped).

Usage: sudo python3 seed_tiered_map.py [--l2-rate-bps N] [--burst-bytes N]
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpfmap    # noqa: E402  bpf syscall (no bpftool fork per tenant: 100 tenants = 200 calls)
import tenants   # noqa: E402

MAP_PIN = "/sys/fs/bpf/tc/globals/tenant_qos_map"
STRUCT = struct.Struct("=I4xQQQQQQ")  # 56 bytes
assert STRUCT.size == 56

_TOPO = tenants.load()                 # $TENANTS_FILE or tenants/default.json
VICTIMS = _TOPO.ips("victim")          # L0, uncapped
ATTACKERS = _TOPO.ips("attacker")      # L2, capped


def update(m, ip, tier, rate, burst):
    # tier, tokens(=burst: bucket starts full), last_update_ns(0 = "freshly seeded"), rate, burst, passed, dropped
    m.update(bpfmap.ip_key(ip), STRUCT.pack(tier, burst, 0, rate, burst, 0, 0))


def verify(m, ip, tier, rate):
    v = m.lookup(bpfmap.ip_key(ip))
    if v is None:
        raise SystemExit(f"verify failed: {ip} missing from tenant_qos_map")
    got_tier, _, _, got_rate, *_ = STRUCT.unpack(v)
    if (got_tier, got_rate) != (tier, rate):
        raise SystemExit(f"verify failed for {ip}: tier={got_tier} rate={got_rate}, wanted tier={tier} rate={rate}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--l2-rate-bps", type=int, default=5_000_000,
                    help="attacker (L2) cap in bytes/s (default 5 MB/s)")
    ap.add_argument("--burst-bytes", type=int, default=0,
                    help="attacker burst; default = max(rate/10, 131072), the rule every arm uses (qos_controller.default_burst)")
    a = ap.parse_args()
    if a.l2_rate_bps <= 0:
        sys.exit("--l2-rate-bps must be > 0 (rate 0 never refills tokens => 100% drop)")
    burst = a.burst_bytes or max(a.l2_rate_bps // 10, 131072)

    m = bpfmap.BPFMap(MAP_PIN)
    for ip in VICTIMS:
        update(m, ip, 0, 0, 0)
        verify(m, ip, 0, 0)
    for ip in ATTACKERS:
        update(m, ip, 2, a.l2_rate_bps, burst)
        verify(m, ip, 2, a.l2_rate_bps)
    print(f"tenant_qos_map seeded: {len(VICTIMS)} victims L0 uncapped, {len(ATTACKERS)} attackers L2 "
          f"rate={a.l2_rate_bps} B/s burst={burst} B")


if __name__ == "__main__":
    main()
