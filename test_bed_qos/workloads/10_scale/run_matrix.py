#!/usr/bin/env python3
"""Workload 10 - many-tenant benchmark (e.g. 100 containers: 95 victims + 5 attackers).

Topology and roles come from a tenants file (TENANTS_FILE, e.g. tenants/scale100.json). The topology and
one victim server container per victim are built ONCE per run; each trial only re-attaches the arm,
starts its controller, launches the floods and measures.

Victim traffic is east-west: every victim tenant runs a fortio client INSIDE its own namespace that calls
the next victim's server (ring), so every victim request crosses tenant ports like mesh traffic.
Attacker j floods victim server j (UDP to the server port); clients whose server is flooded are reported
as "targeted", all others as "bystanders".

CPU plan: harness + controller pinned to --controller-cpus (default 0); tenants (servers, clients,
floods) share --tenant-cpus (default 1-7). Per-CPU busy/softirq time is logged per trial.

Usage:
  sudo TENANTS_FILE=tenants/scale100.json python3 run_matrix.py --reps 2 \\
       --architectures qos_open sidecarless qos_static qos_dynamic_fixed qos_auto_port \\
       --scenarios none a1_flood a5_flood --duration 30 --qps 20
"""
import argparse
import json
import os
import random
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "shared"))
sys.path.insert(0, BASE_DIR)
from workload_lib import log, write_env_json            # noqa: E402
import scale_lib as sl                                   # noqa: E402
import arms                                              # noqa: E402
import bpfmap                                            # noqa: E402
import containers                                        # noqa: E402
import tenants                                           # noqa: E402
import topology                                          # noqa: E402

# scenario -> (number of flooding attackers, hping3 args). Attacker j targets victim server j.
SCENARIOS = {
    "none":     (0, None),
    "a1_flood": (1, "--flood -d 1000"),
    "a5_flood": (5, "--flood -d 1000"),
    "a5_small": (5, "--flood -d 64"),          # packet-rate heavy (small packets)
    "a5pct_flood": ("5%", "--flood -d 1000"),  # 5% of the victim servers flood (at least 1): attack grows with N
    # calibration ladder: 5 attackers at rising rates
    "a5_u1000": (5, "-i u1000 -d 1000"),
    "a5_u200":  (5, "-i u200 -d 1000"),
    "a5_u50":   (5, "-i u50 -d 1000"),
    "a5_u20":   (5, "-i u20 -d 1000"),
    "a5_u10":   (5, "-i u10 -d 1000"),
}
SWEEP = ["none", "a5_u1000", "a5_u200", "a5_u50", "a5_u20", "a5_u10", "a5_flood"]


def n_attackers_for(topo, scenario):
    n = SCENARIOS[scenario][0]
    if isinstance(n, str) and n.endswith("%"):
        return max(1, len(topo.servers()) * int(n[:-1]) // 100)
    return n


def flood_specs(topo, scenario):
    args = SCENARIOS[scenario][1]
    n = n_attackers_for(topo, scenario)
    servers = topo.servers()
    atts = topo.attackers()
    if n > len(atts):
        raise SystemExit(f"scenario {scenario} needs {n} attackers, tenants file has {len(atts)}")
    return [{"ns": a.ns, "dev": a.br_veth, "args": args, "target": servers[j % len(servers)].ip,
             "name": a.name, "ip": a.ip} for j, a in enumerate(atts[:n])]


def ring_pairs(topo):
    """Client -> server pairs. Server i calls server i+1 (ring); a tenant never calls itself (a call to its
    own IP stays on loopback and never crosses a tenant port). Client-only tenants call servers in turn."""
    s = topo.servers()
    pairs = [(s[i], s[(i + 1) % len(s)]) for i in range(len(s)) if len(s) > 1]
    extra = [t for t in topo.clients() if not t.server]
    pairs += [(c, s[k % len(s)]) for k, c in enumerate(extra)] if s else []
    return pairs


def read_hits():
    try:
        v = bpfmap.BPFMap("/sys/fs/bpf/tc/globals/update_counter_map").lookup(bpfmap.u32_key(0))
        return bpfmap.sum_u64(v) if v else None
    except OSError:
        return None


def read_hist():
    """Per-slot invocation counts of the QoS program's run-time histogram (slot s = [2^s, 2^(s+1)) ns)."""
    try:
        m = bpfmap.BPFMap("/sys/fs/bpf/tc/globals/lock_latency_hist")
        return {int.from_bytes(k, "little"): bpfmap.sum_u64(v) for k, v in m.items()}
    except OSError:
        return None


def hist_stats(h0, h1):
    """Mean / p50 / p99 program run time (ns) from the histogram delta; bucket midpoints 1.5 * 2^s."""
    if h0 is None or h1 is None:
        return None, None, None
    d = {s: h1.get(s, 0) - h0.get(s, 0) for s in h1}
    n = sum(v for v in d.values() if v > 0)
    if n <= 0:
        return None, None, None
    mid = lambda s: 1.0 if s == 0 else 1.5 * (2 ** s)   # noqa: E731
    mean = sum(mid(s) * c for s, c in d.items() if c > 0) / n
    acc, p50, p99 = 0, None, None
    for s_ in sorted(d):
        acc += max(d[s_], 0)
        if p50 is None and acc >= 0.5 * n:
            p50 = mid(s_)
        if p99 is None and acc >= 0.99 * n:
            p99 = mid(s_)
    return round(mean, 1), p50, p99


def dev_bytes(dev, what):
    try:
        with open(f"/sys/class/net/{dev}/statistics/{what}") as f:
            return int(f.read())
    except OSError:
        return 0


def controller_stats(path):
    ticks, fails = [], 0
    try:
        for ln in open(path):
            r = json.loads(ln)
            if "tick_ms" in r:
                ticks.append(r["tick_ms"])
            fails += int(r.get("write_failures", 0) or 0)
    except (OSError, ValueError):
        pass
    if not ticks:
        return None, None, fails
    ticks.sort()
    return round(ticks[len(ticks) // 2], 3), round(ticks[min(len(ticks) - 1, int(0.99 * len(ticks)))], 3), fails


def healthy(topo, port):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(32) as ex:
        ok = list(ex.map(lambda t: containers._port_open(t.ip, port, 2), topo.servers()))
    return sum(ok), len(ok)


def set_rps(topo, cpus):
    """Receive Packet Steering on every tenant's host-side veth: packets a tenant sends are processed
    (tc ingress, bridge, delivery) on one of `cpus` chosen by flow hash, like NIC RSS spreads RX work,
    instead of inline on the sending CPU."""
    mask = 0
    for c in tenants._expand_cpus(cpus):
        mask |= 1 << c
    hexmask = format(mask, "x")
    for t in topo.tenants:
        with open(f"/sys/class/net/{t.br_veth}/queues/rx-0/rps_cpus", "w") as f:
            f.write(hexmask)
    return hexmask


def cleanup_after_failure():
    """Kill whatever a failed trial may have left running (floods, clients, controllers)."""
    import subprocess
    for pat in ("hping3", "fortio load", "[q]os_controller"):
        subprocess.run(["pkill", "-9", "-f", pat], capture_output=True)


def run_trial(topo, arch, scenario, rep, raw_root, a):
    trial_id = f"{arch}_{scenario}_rep_{rep}"
    raw = os.path.join(raw_root, trial_id)
    os.makedirs(raw, exist_ok=True)
    log(f"--- trial {trial_id} ---")
    up, total = healthy(topo, topo.server_port)
    if up < total:
        log(f"WARNING: only {up}/{total} servers answer; restarting containers")
        containers.start(topo)

    t_att = time.time()
    arms.attach(arch, BASE_DIR)
    attach_s = round(time.time() - t_att, 2)
    ctrl_log = os.path.join(raw, "controller.jsonl")
    ctrl = arms.start_controller(arch, ctrl_log, BASE_DIR, cpus=a.controller_cpus)

    specs = flood_specs(topo, scenario)
    flood_procs, flood_pps, t_flood = sl.start_verified_floods(
        specs, raw, trial_id, cpus=a.attacker_cpus or a.tenant_cpus, cpu_quota=a.attacker_cpu_quota or None,
        port=topo.server_port, log=log) if specs else ([], {}, time.time())
    with open(os.path.join(raw, "truth.json"), "w") as f:
        json.dump({"scenario": scenario, "start_epoch": t_flood,
                   "attacker_port_labels": sorted(s["dev"] for s in specs),
                   "attacker_ip_labels": sorted(s["ip"] for s in specs),
                   "targets": sorted(s["target"] for s in specs),
                   "flood_verified_pps": flood_pps,
                   "flood_ok": all(v >= 300 for v in flood_pps.values())}, f, indent=2)
    time.sleep(a.warmup)

    ports = [t.br_veth for t in topo.tenants]
    att_devs = [s["dev"] for s in specs]
    targets = {s["target"] for s in specs}
    tgt_devs = [t.br_veth for t in topo.servers() if t.ip in targets]
    cpu = sl.CpuSampler().start()
    t0, rx0, h0, hist0 = time.time(), sl.port_rx_packets(ports), read_hits(), read_hist()
    ab0 = sum(dev_bytes(d, "rx_bytes") for d in att_devs)
    tb0 = sum(dev_bytes(d, "tx_bytes") for d in tgt_devs)

    clients = sl.start_clients(ring_pairs(topo), raw, trial_id, a.qps, a.duration, cpus=a.tenant_cpus,
                               port=topo.server_port)
    sl.wait_clients(clients, a.duration + 20)

    t1, rx1, h1, hist1 = time.time(), sl.port_rx_packets(ports), read_hits(), read_hist()
    ab1 = sum(dev_bytes(d, "rx_bytes") for d in att_devs)
    tb1 = sum(dev_bytes(d, "tx_bytes") for d in tgt_devs)
    util = cpu.stop()
    sl.stop_floods(flood_procs)
    if ctrl is not None:
        ctrl.kill()
        ctrl.wait(5)
    dt = t1 - t0

    allc = sl.merge_fortio([jp for _, jp, _, _ in clients])
    tgt = sl.merge_fortio([jp for _, jp, _, srv in clients if srv.ip in targets]) if targets else None
    bys = sl.merge_fortio([jp for _, jp, _, srv in clients if srv.ip not in targets])
    per = sorted(allc["per_client_p99_ms"])
    tenant_cpus = [f"cpu{c}" for c in tenants._expand_cpus(a.tenant_cpus)] if a.tenant_cpus else list(util)
    ctrl_cpus = [f"cpu{c}" for c in tenants._expand_cpus(str(a.controller_cpus))] if a.controller_cpus is not None else []
    att_cpus = [f"cpu{c}" for c in tenants._expand_cpus(a.attacker_cpus)] if a.attacker_cpus else []
    tick_p50, tick_p99, wfail = controller_stats(ctrl_log)
    prog_mean, prog_p50, prog_p99 = hist_stats(hist0, hist1)
    row = {
        "arch": arch, "scenario": scenario, "rep": rep, "n_tenants": len(topo.tenants),
        "n_victims": len(topo.servers()), "n_attackers_flooding": len(specs), "attach_s": attach_s,
        "prog_ns_mean": prog_mean, "prog_ns_p50": prog_p50, "prog_ns_p99": prog_p99,
        "n_clients": allc["n_clients"], "missing_clients": allc["missing_clients"],
        "requests": allc["requests"], "errors": allc["errors"],
        "actual_qps": allc["actual_qps"], "requested_qps": allc["requested_qps"],
        "p50_ms": allc["p50_ms"], "p90_ms": allc["p90_ms"], "p99_ms": allc["p99_ms"], "p999_ms": allc["p999_ms"],
        "p99_targeted_ms": tgt["p99_ms"] if tgt else None, "p99_bystander_ms": bys["p99_ms"],
        "p999_bystander_ms": bys["p999_ms"],
        "median_client_p99_ms": per[len(per) // 2] if per else None, "worst_client_p99_ms": per[-1] if per else None,
        "flood_ok": all(v >= 300 for v in flood_pps.values()), "flood_pps_total": round(sum(flood_pps.values())),
        "ingress_pps": round((rx1 - rx0) / dt), "hits_per_sec": round((h1 - h0) / dt) if h0 is not None and h1 is not None else None,
        "attack_offered_MBps": round((ab1 - ab0) / dt / 1e6, 3), "to_targets_MBps": round((tb1 - tb0) / dt / 1e6, 3),
        "cpu_busy_tenants": round(sum(util[c]["busy"] for c in tenant_cpus if c in util) / max(1, len(tenant_cpus)), 4),
        "cpu_softirq_tenants": round(sum(util[c]["softirq"] for c in tenant_cpus if c in util) / max(1, len(tenant_cpus)), 4),
        "cpu_busy_controller": round(sum(util[c]["busy"] for c in ctrl_cpus if c in util) / max(1, len(ctrl_cpus)), 4) if ctrl_cpus else None,
        "cpu_busy_attackers": round(sum(util[c]["busy"] for c in att_cpus if c in util) / max(1, len(att_cpus)), 4) if att_cpus else None,
        "ctrl_tick_ms_p50": tick_p50, "ctrl_tick_ms_p99": tick_p99, "ctrl_write_failures": wfail,
    }
    with open(os.path.join(raw, "summary.json"), "w") as f:
        json.dump({**row, "per_client_p99_ms": per, "cpu": util}, f, indent=1)
    log(f"    p99={row['p99_ms']} p99.9={row['p999_ms']} worst_client_p99={row['worst_client_p99_ms']} "
        f"errors={row['errors']} qps={row['actual_qps']}/{row['requested_qps']} ingress={row['ingress_pps']}pps "
        f"cpu={row['cpu_busy_tenants']} flood_ok={row['flood_ok']}")
    return row


COLUMNS = ["arch", "scenario", "rep", "n_tenants", "n_victims", "n_attackers_flooding", "attach_s", "prog_ns_mean",
           "prog_ns_p50", "prog_ns_p99", "n_clients", "missing_clients", "requests", "errors", "actual_qps",
           "requested_qps", "p50_ms", "p90_ms", "p99_ms", "p999_ms", "p99_targeted_ms", "p99_bystander_ms",
           "p999_bystander_ms", "median_client_p99_ms", "worst_client_p99_ms", "flood_ok", "flood_pps_total",
           "ingress_pps", "hits_per_sec", "attack_offered_MBps", "to_targets_MBps", "cpu_busy_tenants",
           "cpu_softirq_tenants", "cpu_busy_controller", "cpu_busy_attackers", "ctrl_tick_ms_p50", "ctrl_tick_ms_p99",
           "ctrl_write_failures"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--architectures", nargs="+", default=["qos_open", "sidecarless", "qos_static",
                                                            "qos_dynamic_fixed", "qos_auto_port"])
    ap.add_argument("--scenarios", nargs="+", default=["none", "a1_flood", "a5_flood"])
    ap.add_argument("--sweep", action="store_true", help=f"calibration ladder {SWEEP} (overrides --scenarios)")
    ap.add_argument("--duration", type=int, default=30, help="client measurement window, s")
    ap.add_argument("--qps", type=float, default=20, help="requests/s per victim client")
    ap.add_argument("--warmup", type=float, default=3.0)
    ap.add_argument("--tenant-cpus", default="1-7")
    ap.add_argument("--controller-cpus", default="0")
    ap.add_argument("--attacker-cpus", default=None,
                    help="cpuset for the floods (default: --tenant-cpus, i.e. attackers share CPUs with victims)")
    ap.add_argument("--attacker-cpu-quota", type=int, default=0, help="CPUQuota %% per flood (0 = unlimited)")
    ap.add_argument("--rps", action="store_true",
                    help="NIC-like receive spreading: RPS on every tenant port, so bridge/delivery softirq work is "
                         "spread over --rps-cpus (like RSS) instead of running on the sender's CPU")
    ap.add_argument("--rps-cpus", default=None, help="CPUs for RPS (default: --tenant-cpus)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--keep-topology", action="store_true", help="leave topology + containers up at the end")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("must run as root")
    if a.controller_cpus is not None:
        os.sched_setaffinity(0, set(tenants._expand_cpus(str(a.controller_cpus))))   # harness off tenant CPUs
    topo = tenants.load()
    scenarios = SWEEP if a.sweep else a.scenarios
    for s in scenarios:
        if s not in SCENARIOS:
            sys.exit(f"unknown scenario {s}; known: {list(SCENARIOS)}")
    for arch in a.architectures:
        arms.resolve(arch)

    run_dir = a.out or os.path.join(HERE, "results", f"{'sweep' if a.sweep else 'run'}_N{a.reps}_{topo.name}_"
                                    f"{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    raw_root = os.path.join(run_dir, "raw")
    os.makedirs(raw_root, exist_ok=True)
    write_env_json(run_dir, a.architectures)
    with open(os.path.join(run_dir, "config.json"), "w") as f:
        json.dump({**vars(a), "scenarios": scenarios, "tenants_file": topo.path,
                   "n_victims": len(topo.victims()), "n_attackers": len(topo.attackers())}, f, indent=2)

    trials = [(arch, s, r) for arch in a.architectures for s in scenarios for r in range(1, a.reps + 1)]
    random.seed(a.seed)
    random.shuffle(trials)
    log(f"Workload 10: {len(trials)} trials, {len(topo.tenants)} tenants ({topo.name}), results -> {run_dir}")

    log("building topology + containers (once per run)...")
    for t in topo.servers():                # victim containers live on the tenant CPU pool
        t.cpus = a.tenant_cpus or t.cpus
    secs = topology.up(topo)
    if a.rps:
        mask = set_rps(topo, a.rps_cpus or a.tenant_cpus)
        log(f"RPS enabled on {len(topo.tenants)} tenant ports, rps_cpus mask {mask}")
    t0 = time.time()
    n = containers.start(topo)
    log(f"topology {secs:.1f}s, {n} containers {time.time() - t0:.1f}s")
    with open(os.path.join(run_dir, "setup.json"), "w") as f:
        json.dump({"topology_s": round(secs, 2), "containers": n, "containers_s": round(time.time() - t0, 2),
                   "n_tenants": len(topo.tenants)}, f, indent=2)

    csv_path = os.path.join(run_dir, "results_summary_raw.csv")
    with open(csv_path, "w") as f:
        f.write(",".join(COLUMNS) + "\n")
    failures, consecutive = [], 0
    try:
        for i, (arch, s, r) in enumerate(trials, 1):
            log(f"[Trial {i}/{len(trials)}] {arch} | {s} | rep {r}")
            try:
                row = run_trial(topo, arch, s, r, raw_root, a)
            except Exception as ex:  # noqa: BLE001  one bad trial must not kill a multi-hour run
                import traceback
                log(f"TRIAL FAILED {arch}/{s}/rep {r}: {type(ex).__name__}: {ex}")
                failures.append({"arch": arch, "scenario": s, "rep": r, "error": f"{type(ex).__name__}: {ex}",
                                 "traceback": traceback.format_exc()})
                with open(os.path.join(run_dir, "failures.json"), "w") as f:
                    json.dump(failures, f, indent=1)
                cleanup_after_failure()
                consecutive += 1
                if consecutive >= 3:
                    log("3 consecutive trial failures: aborting (something is systematically broken)")
                    raise
                continue
            consecutive = 0
            with open(csv_path, "a") as f:
                f.write(",".join("" if row[c] is None else str(row[c]) for c in COLUMNS) + "\n")
    finally:
        sl.stop_floods([])
        if not a.keep_topology:
            containers.stop()
            topology.down(topo)
    log(f"Workload 10 complete: {csv_path}")


if __name__ == "__main__":
    main()
