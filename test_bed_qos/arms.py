#!/usr/bin/env python3
"""arms.py - single source of truth for the experimental arms.

Every runner (workloads/0N_*/run_matrix.py, run_multi_run_matrix.py, markov runner)
must attach eBPF and start controllers through this module so that all arms run in the
same harness and differ only in the factors listed below.

| arm                  | eBPF program        | map          | controller                  | polling            |
|----------------------|---------------------|--------------|-----------------------------|--------------------|
| qos_tiered           | ebpf_qos_tiered.c   | HASH         | none (static seed)          | -                  |
| qos_percpu_static    | ebpf_qos_dynamic.c  | PERCPU_HASH  | none (static seed, same cap)| -                  |
| qos_dynamic_fixed    | ebpf_qos_dynamic.c  | PERCPU_HASH  | qos_controller.py           | fixed 200 ms       |
| qos_dynamic_adaptive | ebpf_qos_dynamic.c  | PERCPU_HASH  | qos_controller.py           | adaptive 20/200/1000 ms |
| qos_markov           | ebpf_qos_dynamic.c  | PERCPU_HASH  | qos_controller_markov.py    | fixed 100 ms       |
| qos_markov_200ms     | ebpf_qos_dynamic.c  | PERCPU_HASH  | qos_controller_markov.py    | fixed 200 ms       |
| qos_auto_ip          | ebpf_qos_auto.c     | PERCPU_HASH  | qos_controller_auto.py ip   | fixed 200 ms       |
| qos_auto_port        | ebpf_qos_auto.c     | PERCPU_HASH  | qos_controller_auto.py port | fixed 200 ms       |
| sidecarless          | ebpf_mesh_router.c  | HASH (1 key) | none (UNPROTECTED baseline) | -                  |
| qos_open             | ebpf_qos_dynamic.c  | PERCPU_HASH  | none, no entries (no limit) | -                  |
| qos_tc_police        | none (kernel tc)    | -            | none (static `tc police`)   | -                  |
| qos_tc_tbf           | none (kernel tc)    | -            | none (static `tc tbf`)      | -                  |

Comparisons: tiered vs percpu_static isolates map sharding; percpu_static vs dynamic_fixed
isolates feedback control; dynamic_fixed vs dynamic_adaptive isolates adaptive polling;
dynamic_fixed vs markov_200ms isolates the DTMC predictor at equal polling rate.

`qos_dynamic` is a deprecated alias of `qos_dynamic_adaptive` (it always ran adaptive polling).
"""
import os
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

ATTACKER_IP = "10.0.0.20"
STATIC_CAP_BPS = int(os.environ.get("TIER_L2_RATE_BPS", "5000000"))  # shared by tiered + percpu_static

ARMS = {
    "qos_tc_police": {"attach": None, "tc": "police", "controller": None},
    "qos_tc_tbf": {"attach": None, "tc": "tbf", "controller": None},
    "qos_tiered": {"attach": "qos_tiered", "controller": None},
    "qos_percpu_static": {"attach": "qos_dynamic", "controller": None, "seed_static": True},
    "qos_dynamic_fixed": {"attach": "qos_dynamic", "controller": "reactive", "args": ["--no-adaptive", "--interval", "0.2"]},
    "qos_dynamic_adaptive": {"attach": "qos_dynamic", "controller": "reactive", "args": ["--interval", "0.2"]},
    "qos_markov": {"attach": "qos_dynamic", "controller": "markov", "args": ["--interval", "0.1"]},
    "qos_markov_200ms": {"attach": "qos_dynamic", "controller": "markov", "args": ["--interval", "0.2"]},
}
DEFAULT_ARMS = ["qos_tc_police", "qos_tc_tbf", "qos_tiered", "qos_percpu_static", "qos_dynamic_fixed", "qos_dynamic_adaptive", "qos_markov", "qos_markov_200ms"]
# Forgetting ablation (Phase 3): same controller/poll rate as qos_markov (100 ms), only memory differs.
for _name, _extra in [("qos_markov_l99", ["--forget-lambda", "0.99"]), ("qos_markov_l95", ["--forget-lambda", "0.95"]),
                      ("qos_markov_l90", ["--forget-lambda", "0.90"]), ("qos_markov_w50", ["--window", "50"])]:
    ARMS[_name] = {"attach": "qos_dynamic", "controller": "markov", "args": ["--interval", "0.1", *_extra]}
FORGET_ARMS = ["qos_markov", "qos_markov_l99", "qos_markov_l95", "qos_markov_l90", "qos_markov_w50", "qos_dynamic_fixed"]
# Automatic role identification (Phase 4). Reactive controller only; fixed 200 ms polling so the
# only difference from qos_dynamic_fixed is WHO gets capped (no victim/attacker list is configured).
AUTO_CAPACITY_PPS = os.environ.get("AUTO_CAPACITY_PPS", "10000")
for _name, _key in [("qos_auto_ip", "ip"), ("qos_auto_port", "port")]:
    ARMS[_name] = {"attach": "qos_auto", "controller": "auto", "auto_key": _key,
                   "args": ["--key", _key, "--capacity-pps", AUTO_CAPACITY_PPS, "--interval", "0.2"]}
AUTO_ARMS = ["qos_dynamic_fixed", "qos_auto_ip", "qos_auto_port"]
# Unprotected baselines (no limiter): without them there is no isolation deficit to measure against.
#   sidecarless : original shared-key program (ebpf_mesh_router.c, 50 contended map updates per packet)
#   qos_open    : the per-CPU program with NO entries, i.e. same data plane as the qos_* arms but no limiting;
#                 separates "better map design" from "the limiter".
ARMS["sidecarless"] = {"attach": "sidecarless", "controller": None, "no_limit": True, "map_check": "flow_map"}
ARMS["qos_open"] = {"attach": "qos_dynamic", "controller": None, "no_limit": True, "map_check": "percpu_rate_limit_map"}
BASELINE_ARMS = ["sidecarless", "qos_open", "qos_tiered", "qos_dynamic_fixed", "qos_auto_port"]
ALIASES = {"qos_dynamic": "qos_dynamic_adaptive"}
CONTROLLER_SCRIPTS = {
    "reactive": os.path.join(BASE_DIR, "qos_controller.py"),
    "markov": os.path.join(BASE_DIR, "markov_experiments", "qos_controller_markov.py"),
    "auto": os.path.join(BASE_DIR, "qos_controller_auto.py"),
}


def resolve(arm):
    arm = ALIASES.get(arm, arm)
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; known: {sorted(ARMS)}")
    return arm


def attach(arm, base_dir=BASE_DIR):
    """Compile/attach the eBPF program for `arm` and apply its static seed."""
    arm = resolve(arm)
    spec = ARMS[arm]
    script, mode = (("attach_tc.sh", spec["tc"]) if spec.get("tc") else ("attach_ebpf.sh", spec["attach"]))
    r = subprocess.run([os.path.join(base_dir, script), mode],
                       cwd=base_dir, capture_output=True, text=True,
                       env={**os.environ, "TIER_L2_RATE_BPS": str(STATIC_CAP_BPS)})
    if r.returncode != 0:
        raise RuntimeError(f"{script} {mode} failed:\n{r.stdout}\n{r.stderr}")
    if spec.get("tc"):
        _verify_tc(arm, spec["tc"])
        return
    if spec.get("seed_static"):
        _seed_percpu_static()
    if spec["controller"] is None:
        verify_maps(arm)


def _seed_percpu_static():
    """Static attacker cap on the per-CPU map, victims uncapped; verifies map entries exist."""
    import qos_controller as qc
    time.sleep(1)
    burst = max(STATIC_CAP_BPS // 10, 65536)
    for v in qc.VICTIM_IPS:
        if not qc.update_tenant_rate_limit(v, rate_bytes_per_sec=0):
            raise RuntimeError(f"failed to seed victim {v} in percpu_rate_limit_map")
    if not qc.update_tenant_rate_limit(ATTACKER_IP, rate_bytes_per_sec=STATIC_CAP_BPS, max_burst_bytes=burst):
        raise RuntimeError("failed to seed attacker in percpu_rate_limit_map")


def _verify_tc(arm, mode):
    if mode == "police":
        out = subprocess.run("tc filter show dev veth-att-br parent ffff:", shell=True, capture_output=True, text=True).stdout
        ok = "police" in out
    else:
        out = subprocess.run("ip netns exec ns_attacker tc qdisc show dev veth-attacker", shell=True, capture_output=True, text=True).stdout
        ok = "tbf" in out
    if not ok:
        raise RuntimeError(f"[{arm}] tc {mode} not installed; tc output: {out!r}")


def verify_maps(arm):
    """Fail loudly if the arm's tenant map does not contain the expected entries."""
    import json as _json
    arm = resolve(arm)
    if ARMS[arm].get("no_limit"):
        # nothing is seeded by design; just require the program's map to exist
        pin = "/sys/fs/bpf/tc/globals/" + ARMS[arm]["map_check"]
        r = subprocess.run(["/usr/sbin/bpftool", "map", "dump", "pinned", pin, "-j"], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"[{arm}] map {ARMS[arm]['map_check']} missing/unreadable: {r.stderr.strip()}")
        return
    if ARMS[arm].get("auto_key"):
        # no tenants are seeded by design; just require that the accounting + limiter maps exist and dump
        for m in ("ip_stats", "port_stats", "percpu_rate_limit_map", "port_rate_limit_map"):
            r = subprocess.run(["/usr/sbin/bpftool", "map", "dump", "pinned", "/sys/fs/bpf/tc/globals/" + m, "-j"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"[{arm}] map {m} missing/unreadable: {r.stderr.strip()}")
        return
    pin = "/sys/fs/bpf/tc/globals/" + ("tenant_qos_map" if arm == "qos_tiered" else "percpu_rate_limit_map")
    out = subprocess.run(["/usr/sbin/bpftool", "map", "dump", "pinned", pin, "-j"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"[{arm}] cannot dump {pin}: {out.stderr.strip()}")
    keys = set()
    for e in _json.loads(out.stdout or "[]"):
        k = e.get("key")
        if k is None:
            k = e.get("formatted", {}).get("key")
        if isinstance(k, list):                      # raw bytes (no BTF): ["0x0a","0x00",...]
            keys.add(".".join(str(int(x, 16)) for x in k))
        elif isinstance(k, int):                     # BTF-formatted u32, native (little-endian) order
            keys.add(".".join(str(b) for b in k.to_bytes(4, "little")))
    need = {"10.0.0.10", "10.0.0.11", "10.0.0.12", ATTACKER_IP}
    if not need <= keys:
        raise RuntimeError(f"[{arm}] map {pin} missing tenants {sorted(need - keys)} (has {sorted(keys)})")


def start_controller(arm, log_file, base_dir=BASE_DIR):
    """Start the arm's controller (returns Popen) or None for static arms."""
    arm = resolve(arm)
    spec = ARMS[arm]
    if spec["controller"] is None:
        return None
    cmd = ["python3", CONTROLLER_SCRIPTS[spec["controller"]], "--log-file", log_file, *spec["args"]]
    proc = subprocess.Popen(cmd)
    time.sleep(1)
    if proc.poll() is not None:
        raise RuntimeError(f"controller for {arm} exited immediately (rc={proc.returncode})")
    verify_maps(arm)  # controller seeds tenants at startup
    return proc


def describe(arm):
    arm = resolve(arm)
    return {"arm": arm, **{k: v for k, v in ARMS[arm].items()}, "static_cap_bps": STATIC_CAP_BPS}


if __name__ == "__main__":
    for a in ARMS:
        print(a, describe(a))
