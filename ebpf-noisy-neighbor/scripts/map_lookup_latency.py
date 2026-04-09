#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes as C
import ctypes.util
import json
import math
import os
import struct
import subprocess
import time

BPF_MAP_LOOKUP_ELEM = 1
BPF_MAP_GET_FD_BY_ID = 14


class BpfAttrMapGetFdById(C.Structure):
    _fields_ = [("map_id", C.c_uint)]


class BpfAttrMapElem(C.Structure):
    _fields_ = [
        ("map_fd", C.c_uint),
        ("pad", C.c_uint),
        ("key", C.c_ulonglong),
        ("value", C.c_ulonglong),
        ("flags", C.c_ulonglong),
    ]


def _sys_bpf_nr() -> int:
    mach = os.uname().machine
    if mach in {"x86_64", "amd64"}:
        return 321
    if mach in {"aarch64", "arm64"}:
        return 280
    return 321


def _map_id_by_name(map_name: str) -> int:
    cp = subprocess.run(["bpftool", "-j", "map", "show"], text=True, capture_output=True, check=False)
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or "bpftool map show failed")
    rows = json.loads(cp.stdout)
    for r in rows:
        if str(r.get("name", "")) == map_name:
            return int(r["id"])
    raise RuntimeError(f"map not found: {map_name}")


def _map_fd_by_id(map_id: int) -> int:
    libc = C.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    attr = BpfAttrMapGetFdById(map_id=map_id)
    fd = libc.syscall(_sys_bpf_nr(), BPF_MAP_GET_FD_BY_ID, C.byref(attr), C.sizeof(attr))
    if fd < 0:
        err = C.get_errno()
        raise OSError(err, os.strerror(err))
    return int(fd)


def _lookup(fd: int, key_buf: C.Array, val_buf: C.Array) -> None:
    libc = C.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    attr = BpfAttrMapElem(
        map_fd=fd,
        pad=0,
        key=C.addressof(key_buf),
        value=C.addressof(val_buf),
        flags=0,
    )
    rc = libc.syscall(_sys_bpf_nr(), BPF_MAP_LOOKUP_ELEM, C.byref(attr), C.sizeof(attr))
    if rc != 0:
        err = C.get_errno()
        # miss is expected for some keys; do not raise for ENOENT
        if err != 2:
            raise OSError(err, os.strerror(err))


def pct(vals: list[float], p: float) -> float:
    if not vals:
        return float("nan")
    vals = sorted(vals)
    i = int(round((p / 100.0) * (len(vals) - 1)))
    return vals[max(0, min(i, len(vals) - 1))]


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure eBPF map lookup latency")
    ap.add_argument("--map-name", default="cgroup_policy_map")
    ap.add_argument("--samples", type=int, default=50000)
    ap.add_argument("--tenant", default="tenant1")
    ap.add_argument("--experiment", required=True)
    ap.add_argument("--key-u64", type=int, default=1)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    map_id = _map_id_by_name(args.map_name)
    fd = _map_fd_by_id(map_id)

    key = C.c_ulonglong(args.key_u64)
    val = (C.c_ubyte * 32)()

    times_us: list[float] = []
    for _ in range(max(args.samples, 100)):
        t0 = time.perf_counter_ns()
        _lookup(fd, key, val)
        t1 = time.perf_counter_ns()
        times_us.append((t1 - t0) / 1000.0)

    avg_us = sum(times_us) / len(times_us)
    p99_us = pct(times_us, 99.0)
    p999_us = pct(times_us, 99.9)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("experiment,tenant,map_lookup_latency_us,p99_lookup_us,p999_lookup_us,map_name,samples\n")
        f.write(
            f"{args.experiment},{args.tenant},{avg_us:.6f},{p99_us:.6f},{p999_us:.6f},{args.map_name},{len(times_us)}\n"
        )

    print(
        f"[map-lookup] experiment={args.experiment} tenant={args.tenant} map={args.map_name} "
        f"avg_us={avg_us:.4f} p99_us={p99_us:.4f} p999_us={p999_us:.4f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
