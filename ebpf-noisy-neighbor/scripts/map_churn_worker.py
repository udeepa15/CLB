#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes as C
import ctypes.util
import json
import os
import random
import subprocess
import time

BPF_MAP_UPDATE_ELEM = 2
BPF_MAP_GET_FD_BY_ID = 14
BPF_ANY = 0


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


def _get_map_id(map_name: str) -> int:
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


def _update_elem(fd: int, key_buf: C.Array, val_buf: C.Array) -> None:
    libc = C.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    attr = BpfAttrMapElem(
        map_fd=fd,
        pad=0,
        key=C.addressof(key_buf),
        value=C.addressof(val_buf),
        flags=BPF_ANY,
    )
    rc = libc.syscall(_sys_bpf_nr(), BPF_MAP_UPDATE_ELEM, C.byref(attr), C.sizeof(attr))
    if rc != 0:
        err = C.get_errno()
        raise OSError(err, os.strerror(err))


def main() -> int:
    ap = argparse.ArgumentParser(description="High-rate eBPF map churn generator")
    ap.add_argument("--map-name", default="cgroup_policy_map")
    ap.add_argument("--duration", type=float, default=20.0)
    ap.add_argument("--key-space", type=int, default=4096)
    ap.add_argument("--target-updates-per-sec", type=int, default=100000)
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    map_id = _get_map_id(args.map_name)
    map_fd = _map_fd_by_id(map_id)

    key = C.c_ulonglong(0)
    val = C.c_uint(300)

    start = time.perf_counter()
    stop_at = start + max(args.duration, 0.1)

    updates = 0
    next_tick = start + 1.0
    per_tick_budget = max(args.target_updates_per_sec, 1000)

    while time.perf_counter() < stop_at:
        tick_start = time.perf_counter()
        for _ in range(per_tick_budget):
            key.value = random.randint(1, max(args.key_space, 2))
            val.value = random.randint(50, 950)
            _update_elem(map_fd, key, val)
            updates += 1
        elapsed = time.perf_counter() - tick_start
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)

        if time.perf_counter() >= next_tick:
            next_tick += 1.0

    total_elapsed = max(time.perf_counter() - start, 1e-9)
    achieved = updates / total_elapsed

    msg = (
        f"[map-churn] map={args.map_name} map_id={map_id} updates={updates} "
        f"elapsed_s={total_elapsed:.3f} achieved_updates_per_sec={achieved:.1f}"
    )
    print(msg)

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write("map_name,map_id,updates,elapsed_s,achieved_updates_per_sec,target_updates_per_sec\n")
            f.write(f"{args.map_name},{map_id},{updates},{total_elapsed:.6f},{achieved:.3f},{args.target_updates_per_sec}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
