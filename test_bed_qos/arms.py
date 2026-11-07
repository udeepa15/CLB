#!/usr/bin/env python3
"""arms.py - single source of truth for the experimental arms.

Every runner (workloads/0N_*/run_matrix.py, run_multi_run_matrix.py, markov runner)
must attach eBPF and start controllers through this module so that all arms run in the
same harness and differ only in the factors listed below.

| arm                  | eBPF program        | limiter                       | controller                  | polling            |
|----------------------|---------------------|-------------------------------|-----------------------------|--------------------|
| qos_tiered           | ebpf_qos_tiered.c   | shared HASH entry, no lock    | none (static seed)          | -                  |
| qos_static           | ebpf_qos_dynamic.c  | exact GCRA (qos_limiter.h)    | none (static seed, same cap)| -                  |
| qos_dynamic_fixed    | ebpf_qos_dynamic.c  | exact GCRA                    | qos_controller.py           | fixed 200 ms       |
| qos_dynamic_adaptive | ebpf_qos_dynamic.c  | exact GCRA                    | qos_controller.py           | adaptive 20/200/1000 ms |
| qos_markov           | ebpf_qos_dynamic.c  | exact GCRA                    | qos_controller_markov.py    | fixed 100 ms       |
| qos_markov_200ms     | ebpf_qos_dynamic.c  | exact GCRA                    | qos_controller_markov.py    | fixed 200 ms       |
| qos_auto_ip          | ebpf_qos_auto.c     | exact GCRA, key = source IP   | qos_controller_auto.py ip   | fixed 200 ms       |
| qos_auto_port        | ebpf_qos_auto.c     | exact GCRA, key = ingress port| qos_controller_auto.py port | fixed 200 ms       |
| sidecarless          | ebpf_mesh_router.c  | none; 1 shared key, 50 upd/pkt| none (UNPROTECTED baseline) | -                  |
| qos_open             | ebpf_qos_dynamic.c  | none (empty config map)       | none                        | -                  |
| qos_tc_police        | none (kernel tc)    | tc police                     | none (static)               | -                  |
| qos_tc_tbf           | none (kernel tc)    | tc tbf                        | none (static)               | -                  |

All eBPF limiters enforce at the tenant's own bridge port (ingress), so each packet is charged once.
Every capped arm uses the same burst rule (qos_controller.default_burst), so arms differ only in the
rate and in who sets it.

Comparisons: tiered vs static isolates the limiter design (unlocked shared entry written by every
packet vs read-only config + kernel-owned exact bucket); static vs dynamic_fixed isolates feedback
control; dynamic_fixed vs dynamic_adaptive isolates adaptive polling; dynamic_fixed vs markov_200ms
isolates the DTMC predictor at equal polling rate; qos_open vs sidecarless isolates the data-plane
map design with no limiting at all.

`qos_dynamic` is a deprecated alias of `qos_dynamic_adaptive` (it always ran adaptive polling);
`qos_percpu_static` is the old name of `qos_static` (its limiter is no longer per-CPU).
"""
import os
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

STATIC_CAP_BPS = int(os.environ.get("TIER_L2_RATE_BPS", "5000000"))  # shared by tiered + static

ARMS = {
    "qos_tc_police": {"attach": None, "tc": "police", "controller": None},
    "qos_tc_tbf": {"attach": None, "tc": "tbf", "controller": None},
    "qos_tiered": {"attach": "qos_tiered", "controller": None},
    "qos_static": {"attach": "qos_dynamic", "controller": None, "seed_static": True},
    "qos_dynamic_fixed": {"attach": "qos_dynamic", "controller": "reactive", "args": ["--no-adaptive", "--interval", "0.2"]},
    "qos_dynamic_adaptive": {"attach": "qos_dynamic", "controller": "reactive", "args": ["--interval", "0.2"]},
    "qos_markov": {"attach": "qos_dynamic", "controller": "markov", "args": ["--interval", "0.1"]},
    "qos_markov_200ms": {"attach": "qos_dynamic", "controller": "markov", "args": ["--interval", "0.2"]},
}
DEFAULT_ARMS = ["qos_tc_police", "qos_tc_tbf", "qos_tiered", "qos_static", "qos_dynamic_fixed", "qos_dynamic_adaptive", "qos_markov", "qos_markov_200ms"]
# Forgetting ablation (Phase 3): same controller/poll rate as qos_markov (100 ms), only memory differs.
for _name, _extra in [("qos_markov_l99", ["--forget-lambda", "0.99"]), ("qos_markov_l95", ["--forget-lambda", "0.95"]),
                      ("qos_markov_l90", ["--forget-lambda", "0.90"]), ("qos_markov_w50", ["--window", "50"])]:
    ARMS[_name] = {"attach": "qos_dynamic", "controller": "markov", "args": ["--interval", "0.1", *_extra]}
FORGET_ARMS = ["qos_markov", "qos_markov_l99", "qos_markov_l95", "qos_markov_l90", "qos_markov_w50", "qos_dynamic_fixed"]
# Automatic role identification (Phase 4). Reactive controller only; fixed 200 ms polling so the
# only difference from qos_dynamic_fixed is WHO gets capped (no victim/attacker list is configured).
import calibration as _calibration  # noqa: E402
AUTO_CAPACITY_PPS = os.environ.get("AUTO_CAPACITY_PPS") or str(int(_calibration.load()["capacity_pps"]))
for _name, _key in [("qos_auto_ip", "ip"), ("qos_auto_port", "port")]:
    ARMS[_name] = {"attach": "qos_auto", "controller": "auto", "auto_key": _key,
                   "args": ["--key", _key, "--capacity-pps", AUTO_CAPACITY_PPS, "--interval", "0.2"]}
AUTO_ARMS = ["qos_dynamic_fixed", "qos_auto_ip", "qos_auto_port"]
# Unprotected baselines (no limiter): without them there is no isolation deficit to measure against.
#   sidecarless : original shared-key program (ebpf_mesh_router.c, 50 contended map updates per packet)
#   qos_open    : the per-CPU program with NO entries, i.e. same data plane as the qos_* arms but no limiting;
#                 separates "better map design" from "the limiter".
ARMS["sidecarless"] = {"attach": "sidecarless", "controller": None, "no_limit": True, "map_check": "flow_map"}
ARMS["qos_open"] = {"attach": "qos_dynamic", "controller": None, "no_limit": True, "map_check": "rate_cfg_map"}
BASELINE_ARMS = ["sidecarless", "qos_open", "qos_tiered", "qos_dynamic_fixed", "qos_auto_port"]
ALIASES = {"qos_dynamic": "qos_dynamic_adaptive", "qos_percpu_static": "qos_static"}
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
    """Static attacker cap in rate_cfg_map (victims uncapped), same burst rule as the controllers."""
    import qos_controller as qc
    for v in qc.VICTIM_IPS:
        if not qc.update_tenant_rate_limit(v, rate_bytes_per_sec=0):
            raise RuntimeError(f"failed to seed victim {v} in rate_cfg_map")
    for a in qc.ATTACKER_IPS:
        if not qc.update_tenant_rate_limit(a, rate_bytes_per_sec=STATIC_CAP_BPS):
            raise RuntimeError(f"failed to seed attacker {a} in rate_cfg_map")


def _verify_tc(arm, mode):
    import tenants
    for t in tenants.load().attackers():
        if mode == "police":
            out = subprocess.run(["tc", "filter", "show", "dev", t.br_veth, "parent", "ffff:"], capture_output=True, text=True).stdout
            ok = "police" in out
        else:
            out = subprocess.run(["ip", "netns", "exec", t.ns, "tc", "qdisc", "show", "dev", t.ns_veth], capture_output=True, text=True).stdout
            ok = "tbf" in out
        if not ok:
            raise RuntimeError(f"[{arm}] tc {mode} not installed for {t.name}; tc output: {out!r}")


def _pinned_keys(name):
    import bpfmap
    return bpfmap.BPFMap("/sys/fs/bpf/tc/globals/" + name).keys()


def verify_maps(arm):
    """Fail loudly if the arm's maps are missing or its tenant config does not hold the expected entries."""
    import bpfmap
    arm = resolve(arm)
    spec = ARMS[arm]
    try:
        if spec.get("no_limit"):
            keys = _pinned_keys(spec["map_check"])       # must exist; qos_open must also be empty
            if spec["map_check"] == "rate_cfg_map" and keys:
                raise RuntimeError(f"[{arm}] rate_cfg_map should be empty but has {len(keys)} entries")
            return
        if spec.get("auto_key"):
            for m in ("ip_stats", "port_stats", "rate_cfg_map", "port_cfg_map"):
                _pinned_keys(m)                          # no tenants are seeded by design; maps must exist
            return
        if arm == "qos_tiered":
            keys = {bpfmap.ip_from_key(k) for k in _pinned_keys("tenant_qos_map")}
        else:
            keys = {bpfmap.ip_from_key(k) for k in _pinned_keys("rate_cfg_map")}
    except OSError as e:
        raise RuntimeError(f"[{arm}] map check failed: {e}") from e
    import qos_controller as qc
    need = set(qc.VICTIM_IPS) | set(qc.ATTACKER_IPS)
    if not need <= keys:
        raise RuntimeError(f"[{arm}] tenant config missing {sorted(need - keys)} (has {sorted(keys)})")


def start_controller(arm, log_file, base_dir=BASE_DIR, cpus=None):
    """Start the arm's controller (returns Popen) or None for static arms.
    cpus: optional cpuset (e.g. "0") so the controller does not compete with tenants for CPU."""
    arm = resolve(arm)
    spec = ARMS[arm]
    if spec["controller"] is None:
        return None
    cmd = (["taskset", "-c", str(cpus)] if cpus is not None else []) + \
          ["python3", CONTROLLER_SCRIPTS[spec["controller"]], "--log-file", log_file, *spec["args"]]
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
