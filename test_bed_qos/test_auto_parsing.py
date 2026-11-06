#!/usr/bin/env python3
"""Offline tests for the bpftool-output parsers in qos_controller_auto.py.
NOTE: the JSON fixtures below are my reconstruction of bpftool v5.15 output for BTF and non-BTF
maps; they could not be checked against the real tool here (no root). The smoke test
(`sudo python3 smoke_test.py --arms qos_auto_ip qos_auto_port`) is the real verification."""
import json, os, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qos_controller_auto as a

def le(n): return list(int(n).to_bytes(8, "little"))

def test_btf_formatted():
    d = json.dumps([{"key": 167772170, "formatted": {"key": 167772170, "values": [
        {"cpu": 0, "value": {"packets": 10, "bytes": 600}}, {"cpu": 1, "value": {"packets": 5, "bytes": 300}}]}},
        {"key": 335544330, "values": [{"cpu": 0, "value": {"packets": 7, "bytes": 1}}]}])
    r = a.parse_stats_dump(d)
    assert r == {167772170: 15, 335544330: 7}, r
    assert a.key_to_ip(167772170) == "10.0.0.10" and a.key_to_ip(335544330) == "10.0.0.20"

def test_raw_hex():
    d = json.dumps([{"key": ["0x0a", "0x00", "0x00", "0x14"], "values": [
        {"cpu": 0, "value": [hex(b) for b in le(3) + le(99)]}, {"cpu": 1, "value": [hex(b) for b in le(4) + le(99)]}]}])
    r = a.parse_stats_dump(d)
    assert r == {0x1400000a: 7}, r
    assert a.key_to_ip(0x1400000a) == "10.0.0.20"

def test_port_int_key_and_empty():
    assert a.parse_stats_dump("[]") == {} and a.parse_stats_dump("") == {}
    assert a.parse_stats_dump(json.dumps([{"key": 7, "values": [{"cpu": 0, "value": {"packets": 1, "bytes": 1}}]}])) == {7: 1}

def test_unrecognised_raises():
    try: a.parse_stats_dump(json.dumps([{"key": "weird", "values": []}]))
    except ValueError: return
    raise AssertionError

def test_bridge_port_regex():
    import re
    line = "7: veth-att-br@if6: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 master br-mesh state UP"
    m = re.match(r"\s*(\d+):\s+([^:@\s]+)", line)
    assert (int(m.group(1)), m.group(2)) == (7, "veth-att-br")

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"): f(); print("PASS", n)
