#!/usr/bin/env python3
"""Utility to update/read tc eBPF maps for adaptive control."""

from __future__ import annotations

import argparse
import ipaddress
import json
import struct
import subprocess
import sys
from typing import Iterable


_MAP_ID_CACHE: dict[str, int] = {}


# Linux BPF object names are limited (BPF_OBJ_NAME_LEN = 16 including NUL),
# so long map names can appear truncated in `bpftool map show` output.
_MAP_ALIASES: dict[str, list[str]] = {
    "control_map": ["control_map"],
    "stats_map": ["stats_map"],
    "cgroup_policy_map": ["cgroup_policy_map", "cgroup_policy_m"],
}


def _hex_bytes(data: bytes) -> list[str]:
    return [f"{b:02x}" for b in data]


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=True, text=True, capture_output=True)


def _candidate_map_names(map_name: str) -> list[str]:
    candidates: list[str] = []
    for name in _MAP_ALIASES.get(map_name, [map_name]):
        if name not in candidates:
            candidates.append(name)

    truncated = map_name[:15]
    if truncated not in candidates:
        candidates.append(truncated)

    return candidates


def _resolve_map_id(map_name: str) -> int:
    cached = _MAP_ID_CACHE.get(map_name)
    if cached is not None:
        return cached

    cp = _run(["bpftool", "-j", "map", "show"])
    maps = json.loads(cp.stdout or "[]")
    candidates = set(_candidate_map_names(map_name))
    for rec in maps:
        rec_name = str(rec.get("name", ""))
        if rec_name in candidates:
            map_id = int(rec["id"])
            _MAP_ID_CACHE[map_name] = map_id
            return map_id

    available = sorted(str(rec.get("name", "")) for rec in maps)
    raise RuntimeError(
        f"BPF map not found for '{map_name}'. Tried names={sorted(candidates)}; "
        f"available={available}"
    )


def _map_selector(map_name: str) -> list[str]:
    """Use map id for compatibility with older/newer bpftool parser variants."""
    return ["id", str(_resolve_map_id(map_name))]


def _bpftool_update(map_name: str, key: bytes, value: bytes) -> None:
    cmd = [
        "bpftool",
        "map",
        "update",
        *_map_selector(map_name),
        "key",
        "hex",
        *(_hex_bytes(key)),
        "value",
        "hex",
        *(_hex_bytes(value)),
    ]
    _run(cmd)


def _bpftool_lookup(map_name: str, key: bytes) -> dict | None:
    cmd = [
        "bpftool",
        "-j",
        "map",
        "lookup",
        *_map_selector(map_name),
        "key",
        "hex",
        *(_hex_bytes(key)),
    ]
    cp = subprocess.run(cmd, text=True, capture_output=True)
    if cp.returncode != 0:
        return None
    out = cp.stdout.strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def _bpftool_delete(map_name: str, key: bytes) -> None:
    cmd = [
        "bpftool",
        "map",
        "delete",
        *_map_selector(map_name),
        "key",
        "hex",
        *(_hex_bytes(key)),
    ]
    subprocess.run(cmd, check=False, text=True, capture_output=True)


def _u32(v: int) -> bytes:
    return struct.pack("<I", v)


def _u64(v: int) -> bytes:
    return struct.pack("<Q", v)


def cmd_set_global(args: argparse.Namespace) -> int:
    noisy_ip_be = int(ipaddress.IPv4Address(args.noisy_ip))
    identity_mode = 1 if args.identity_mode == "cgroup" else 0

    # struct control_cfg {u32 drop_rate; u32 identity_mode; u32 noisy_ip_be; u32 reserved;}
    value = struct.pack("<IIII", args.drop_rate, identity_mode, noisy_ip_be, 0)
    _bpftool_update("control_map", _u32(0), value)
    return 0


def cmd_set_cgroup(args: argparse.Namespace) -> int:
    _bpftool_update("cgroup_policy_map", _u64(args.cgroup_id), _u32(args.drop_rate))
    return 0


def cmd_clear_cgroup(args: argparse.Namespace) -> int:
    _bpftool_delete("cgroup_policy_map", _u64(args.cgroup_id))
    return 0


def _value_hex_to_bytes(hex_values: Iterable[str]) -> bytes:
    return bytes(int(x, 16) for x in hex_values)


def cmd_stats(_: argparse.Namespace) -> int:
    rec = _bpftool_lookup("stats_map", _u32(0))
    if not rec:
        print(json.dumps({"total": 0, "dropped": 0, "passed": 0}))
        return 0

    value_hex = rec.get("value", [])
    raw = _value_hex_to_bytes(value_hex)
    if len(raw) < 24:
        print(json.dumps({"total": 0, "dropped": 0, "passed": 0}))
        return 0

    total, dropped, passed = struct.unpack("<QQQ", raw[:24])
    print(json.dumps({"total": total, "dropped": dropped, "passed": passed}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Adaptive eBPF map control")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_global = sub.add_parser("set-global", help="Update global adaptive policy")
    p_global.add_argument("--drop-rate", type=int, required=True, help="Drop rate in per-mille (0..1000)")
    p_global.add_argument("--identity-mode", choices=["ip", "cgroup"], required=True)
    p_global.add_argument("--noisy-ip", default="10.0.0.4")
    p_global.set_defaults(func=cmd_set_global)

    p_cg = sub.add_parser("set-cgroup", help="Set cgroup drop policy")
    p_cg.add_argument("--cgroup-id", type=int, required=True)
    p_cg.add_argument("--drop-rate", type=int, required=True, help="Drop rate in per-mille (0..1000)")
    p_cg.set_defaults(func=cmd_set_cgroup)

    p_cg_del = sub.add_parser("clear-cgroup", help="Delete cgroup policy entry")
    p_cg_del.add_argument("--cgroup-id", type=int, required=True)
    p_cg_del.set_defaults(func=cmd_clear_cgroup)

    p_stats = sub.add_parser("stats", help="Read adaptive stats map")
    p_stats.set_defaults(func=cmd_stats)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if getattr(args, "drop_rate", 0) is not None:
        drop = getattr(args, "drop_rate", 0)
        if drop is not None and (drop < 0 or drop > 1000):
            print("drop-rate must be in [0, 1000]", file=sys.stderr)
            return 2

    try:
        return int(args.func(args))
    except RuntimeError as exc:
        print(f"bpftool map resolution failed: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        msg = exc.stderr.strip() if exc.stderr else str(exc)
        print(f"bpftool operation failed: {msg}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
