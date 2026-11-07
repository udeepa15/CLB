#!/usr/bin/env python3
"""Self-test for bpfmap.py against real kernel maps (needs root): sudo python3 test_bpfmap.py

Creates temporary maps of each type under /sys/fs/bpf, exercises update/lookup/delete/keys/items
(batch and fallback paths), and cross-checks the byte layout against bpftool's own dump.
"""
import ctypes
import json
import os
import struct
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpfmap  # noqa: E402

PIN_DIR = "/sys/fs/bpf"


def pin(fd, path):
    p = ctypes.create_string_buffer(os.fsencode(path))
    attr = bpfmap._attr()
    struct.pack_into("=QII", attr, 0, ctypes.addressof(p), fd, 0)
    bpfmap._bpf(6, attr)  # BPF_OBJ_PIN


def make(name, mtype, ksz, vsz, n):
    path = f"{PIN_DIR}/bpfmap_selftest_{name}"
    if os.path.exists(path):
        os.unlink(path)
    fd = bpfmap.create_map(mtype, ksz, vsz, n)
    pin(fd, path)
    os.close(fd)
    return path


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_hash(path):
    m = bpfmap.BPFMap(path)
    check(m.type == 1 and not m.percpu and m.key_size == 4 and m.value_size == 16, "hash info")
    k = bpfmap.ip_key("10.0.0.20")
    check(m.lookup(k) is None, "absent key returns None")
    m.update(k, struct.pack("=QQ", 5_000_000, 500_000))
    check(struct.unpack("=QQ", m.lookup(k)) == (5_000_000, 500_000), "hash round trip")
    for i in range(300):  # more than one batch chunk
        m.update(bpfmap.u32_key(i + 1000), struct.pack("=QQ", i, 2 * i))
    items = dict(m.items())
    check(len(items) == 301 and struct.unpack("=QQ", items[bpfmap.u32_key(1299)]) == (299, 598), "batch items")
    m._batch_ok = False
    check(dict(m.items()) == items, "fallback iteration equals batch")
    check(set(m.keys()) == set(items), "keys")
    check(m.delete(k) and not m.delete(k), "delete")
    # layout cross-check with bpftool (raw hex bytes of the value)
    out = json.loads(subprocess.run(["/usr/sbin/bpftool", "map", "lookup", "pinned", path, "key", "232", "3", "0", "0",
                                     "-j"], capture_output=True, text=True).stdout)
    raw = bytes(int(x, 16) for x in out["value"])
    check(struct.unpack("=QQ", raw) == (0, 0), "bpftool sees the same bytes (key 1000 -> value 0,0)")


def test_percpu(path, mtype):
    m = bpfmap.BPFMap(path)
    n = bpfmap.possible_cpus()
    check(m.percpu and m.ncpus == n, "percpu info")
    k = bpfmap.u32_key(7)
    m.update(k, struct.pack("=QQ", 1, 2))                       # replicated to all CPUs
    v = m.lookup(k)
    check(len(v) == n and all(struct.unpack("=QQ", x) == (1, 2) for x in v), "replicated percpu value")
    m.update(k, [struct.pack("=QQ", c, 10 * c) for c in range(n)])  # one value per CPU
    check(bpfmap.sum_u64(m.lookup(k), 0) == sum(range(n)), "per-CPU sum (offset 0)")
    check(bpfmap.sum_u64(m.lookup(k), 8) == 10 * sum(range(n)), "per-CPU sum (offset 8)")
    for i in range(100):
        m.update(bpfmap.u32_key(100 + i), struct.pack("=QQ", i, 0))
    items = dict(m.items())
    check(len(items) == 101 and bpfmap.sum_u64(items[bpfmap.u32_key(150)]) == 50 * n, f"percpu items type {mtype}")
    out = json.loads(subprocess.run(["/usr/sbin/bpftool", "map", "lookup", "pinned", path, "key", "7", "0", "0", "0",
                                     "-j"], capture_output=True, text=True).stdout)
    per_cpu = [bytes(int(x, 16) for x in e["value"]) for e in out["values"]]
    check([struct.unpack("=QQ", x) for x in per_cpu] == [(c, 10 * c) for c in range(n)], "bpftool agrees per CPU")


def test_percpu_array(path):
    m = bpfmap.BPFMap(path)
    m.update(bpfmap.u32_key(0), [struct.pack("=Q", c + 1) for c in range(m.ncpus)])
    check(bpfmap.sum_u64(m.lookup(bpfmap.u32_key(0))) == sum(range(1, m.ncpus + 1)), "percpu array")
    check(len(m.items()) == 1, "array items")


def test_open_pinned_reopens(path):
    a = bpfmap.open_pinned(path)
    check(bpfmap.open_pinned(path) is a, "cached")
    os.unlink(path)
    path2 = make("hash", 1, 4, 16, 1024)
    check(path2 == path, "same path")
    b = bpfmap.open_pinned(path)
    check(b is not a and b.lookup(bpfmap.ip_key("10.0.0.20")) is None, "re-pinned map is reopened")


if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("needs root: sudo python3 test_bpfmap.py")
    paths = [make("hash", 1, 4, 16, 1024), make("pch", 5, 4, 16, 1024), make("lru", 10, 4, 16, 1024),
             make("pca", 6, 4, 8, 1)]
    try:
        test_hash(paths[0]); print("PASS hash (+ batch, fallback, bpftool layout)")
        test_percpu(paths[1], 5); print("PASS percpu_hash (+ bpftool per-CPU layout)")
        test_percpu(paths[2], 10); print("PASS lru_percpu_hash")
        test_percpu_array(paths[3]); print("PASS percpu_array")
        test_open_pinned_reopens(paths[0]); print("PASS open_pinned reopens a replaced pin")
    finally:
        for p in paths:
            if os.path.exists(p):
                os.unlink(p)
    print("ALL PASS")
