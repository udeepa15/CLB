#!/usr/bin/env python3
"""run_timevarying.py - time-varying attacks at scale (workload 10 topology, e.g. 100 containers).

Every attacker in the tenants file floods its own victim server (attacker j -> server j), and all of them
follow the same rate schedule in sync (scale_lib.PatternDriver). Each scenario is a list of phases; every
phase has its own client window, so victim latency and attacker goodput are measured per phase.

  bursty : 1 s flood / 3 s near-idle, repeated (reaction speed: 20 ms adaptive vs 200 ms vs DTMC)
  ramp   : u1000 -> u200 -> u50 -> u20 -> u10 -> flood, 6 s each (tracking an escalating attacker)
  regime : 30 s "calm" (u100, ~9 MB/s per attacker: above the 5 MB/s static cap, near-tolerable)
           then 30 s flood (efficiency in calm vs protection after the switch; DTMC forgetting)

Why two metrics: a static cap enforces all the time, so on victim latency the controllers can at best tie
it. What adaptive control can add is efficiency - letting a heavy tenant through while it harms nobody.
So every phase records victim P99 (protection) AND the bytes the attackers got to their targets (goodput).

Usage:
  sudo TENANTS_FILE=tenants/scale100.json QOS_CALIBRATION=../../calibration/scale100_rps.json \\
       python3 run_timevarying.py --reps 10 --tenant-cpus 1-5 --attacker-cpus 6-7 --rps
"""
import argparse
import json
import os
import random
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_matrix as rm                                   # noqa: E402  (shared helpers, same measurements)
from run_matrix import arms, containers, tenants, topology, sl, log, write_env_json  # noqa: E402

FLOOD, IDLE = "--flood -d 1000", "-i u1000 -d 1000"
TV = {   # scenario -> [(phase name, seconds, [(step seconds, hping3 args)], repeat)]
    "bursty": [("all", 40, [(1.0, FLOOD), (3.0, IDLE)], True)],
    "ramp":   [("all", 36, [(6, "-i u1000 -d 1000"), (6, "-i u200 -d 1000"), (6, "-i u50 -d 1000"),
                            (6, "-i u20 -d 1000"), (6, "-i u10 -d 1000"), (6, FLOOD)], False)],
    "regime": [("calm", 30, [(30, "-i u100 -d 1000")], False), ("attack", 30, [(30, FLOOD)], False)],
}
DEFAULT_ARMS = ["qos_open", "qos_static", "qos_tc_police", "qos_dynamic_fixed", "qos_dynamic_adaptive",
                "qos_markov", "qos_markov_200ms", "qos_markov_l95", "qos_auto_port"]
PHASE_COLS = ["arch", "scenario", "rep", "phase", "seconds", "requests", "errors", "p50_ms", "p99_ms", "p999_ms",
              "p99_targeted_ms", "p99_bystander_ms", "worst_client_p99_ms", "attack_offered_MBps", "to_targets_MBps",
              "goodput_fraction", "ingress_pps", "hits_per_sec", "cpu_busy_tenants", "cpu_softirq_tenants", "flood_ok"]
TRIAL_COLS = ["arch", "scenario", "rep", "n_tenants", "n_attackers", "attach_s", "requests", "errors", "p50_ms",
              "p99_ms", "p999_ms", "worst_client_p99_ms", "attack_offered_MBps", "to_targets_MBps", "flood_ok",
              "ctrl_tick_ms_p50", "ctrl_tick_ms_p99", "ctrl_write_failures", "prog_ns_mean"]


def cpu_avg(util, cpus, key):
    names = [f"cpu{c}" for c in tenants._expand_cpus(cpus)] if cpus else list(util)
    vals = [util[c][key] for c in names if c in util]
    return round(sum(vals) / len(vals), 4) if vals else None


def run_trial(topo, arch, scenario, rep, raw_root, a):
    trial_id = f"{arch}_{scenario}_rep_{rep}"
    raw = os.path.join(raw_root, trial_id)
    os.makedirs(raw, exist_ok=True)
    log(f"--- trial {trial_id} ---")
    up, total = rm.healthy(topo, topo.server_port)
    if up < total:
        log(f"WARNING: only {up}/{total} servers answer; restarting containers")
        containers.start(topo)
    t_att = time.time()
    BASE = rm.BASE_DIR
    arms.attach(arch, BASE)
    attach_s = round(time.time() - t_att, 2)
    ctrl_log = os.path.join(raw, "controller.jsonl")
    ctrl = arms.start_controller(arch, ctrl_log, BASE, cpus=a.controller_cpus)

    servers = topo.servers()
    specs = [{"ns": t.ns, "dev": t.br_veth, "target": servers[j % len(servers)].ip, "ip": t.ip}
             for j, t in enumerate(topo.attackers())]
    targets = {s["target"] for s in specs}
    att_devs = [s["dev"] for s in specs]
    tgt_devs = [t.br_veth for t in servers if t.ip in targets]
    ports = [t.br_veth for t in topo.tenants]
    phases, all_clients, drivers, t_first = [], [], [], None
    hist0 = rm.read_hist()
    for k, (pname, secs, schedule, repeat) in enumerate(TV[scenario]):
        drv = sl.PatternDriver(specs, schedule, raw, f"{trial_id}_{pname}", cpus=a.attacker_cpus or a.tenant_cpus,
                               port=topo.server_port, repeat=repeat, lead_s=a.warmup if k == 0 else 0.0, log=log)
        drv.start()
        drivers.append(drv)
        t_first = t_first or time.time()
        if k == 0:
            time.sleep(a.warmup)
        cpu = sl.CpuSampler().start()
        t0, rx0, h0 = time.time(), sl.port_rx_packets(ports), rm.read_hits()
        ab0, tb0 = sum(rm.dev_bytes(d, "rx_bytes") for d in att_devs), sum(rm.dev_bytes(d, "tx_bytes") for d in tgt_devs)
        clients = sl.start_clients(rm.ring_pairs(topo), raw, f"{trial_id}_{pname}", a.qps, secs, cpus=a.tenant_cpus,
                                   port=topo.server_port)
        sl.wait_clients(clients, secs + 20)
        t1, rx1, h1 = time.time(), sl.port_rx_packets(ports), rm.read_hits()
        ab1, tb1 = sum(rm.dev_bytes(d, "rx_bytes") for d in att_devs), sum(rm.dev_bytes(d, "tx_bytes") for d in tgt_devs)
        util = cpu.stop()
        drv.stop()
        dt = t1 - t0
        m = sl.merge_fortio([jp for _, jp, _, _ in clients])
        tg = sl.merge_fortio([jp for _, jp, _, s in clients if s.ip in targets])
        by = sl.merge_fortio([jp for _, jp, _, s in clients if s.ip not in targets])
        offered, delivered = (ab1 - ab0) / dt / 1e6, (tb1 - tb0) / dt / 1e6
        per = sorted(m["per_client_p99_ms"])
        phases.append({"arch": arch, "scenario": scenario, "rep": rep, "phase": pname, "seconds": round(dt, 2),
                       "requests": m["requests"], "errors": m["errors"], "p50_ms": m["p50_ms"], "p99_ms": m["p99_ms"],
                       "p999_ms": m["p999_ms"], "p99_targeted_ms": tg["p99_ms"], "p99_bystander_ms": by["p99_ms"],
                       "worst_client_p99_ms": per[-1] if per else None,
                       "attack_offered_MBps": round(offered, 3), "to_targets_MBps": round(delivered, 3),
                       "goodput_fraction": round(delivered / offered, 4) if offered > 0 else None,
                       "ingress_pps": round((rx1 - rx0) / dt),
                       "hits_per_sec": round((h1 - h0) / dt) if h0 is not None and h1 is not None else None,
                       "cpu_busy_tenants": cpu_avg(util, a.tenant_cpus, "busy"),
                       "cpu_softirq_tenants": cpu_avg(util, a.tenant_cpus, "softirq"), "flood_ok": drv.ok})
        all_clients += [jp for _, jp, _, _ in clients]
    hist1 = rm.read_hist()
    if ctrl is not None:
        ctrl.kill()
        ctrl.wait(5)
    sl.stop_floods([])
    with open(os.path.join(raw, "truth.json"), "w") as f:
        json.dump({"scenario": scenario, "start_epoch": t_first,
                   "attacker_port_labels": sorted(att_devs), "attacker_ip_labels": sorted(s["ip"] for s in specs),
                   "targets": sorted(targets), "steps": [st for d in drivers for st in d.steps],
                   "flood_ok": all(d.ok for d in drivers)}, f, indent=1)
    allm = sl.merge_fortio(all_clients)
    per = sorted(allm["per_client_p99_ms"])
    tick_p50, tick_p99, wfail = rm.controller_stats(ctrl_log)
    total_s = sum(p["seconds"] for p in phases)
    row = {"arch": arch, "scenario": scenario, "rep": rep, "n_tenants": len(topo.tenants), "n_attackers": len(specs),
           "attach_s": attach_s, "requests": allm["requests"], "errors": allm["errors"], "p50_ms": allm["p50_ms"],
           "p99_ms": allm["p99_ms"], "p999_ms": allm["p999_ms"], "worst_client_p99_ms": per[-1] if per else None,
           "attack_offered_MBps": round(sum(p["attack_offered_MBps"] * p["seconds"] for p in phases) / total_s, 3),
           "to_targets_MBps": round(sum(p["to_targets_MBps"] * p["seconds"] for p in phases) / total_s, 3),
           "flood_ok": all(d.ok for d in drivers), "ctrl_tick_ms_p50": tick_p50, "ctrl_tick_ms_p99": tick_p99,
           "ctrl_write_failures": wfail, "prog_ns_mean": rm.hist_stats(hist0, hist1)[0]}
    with open(os.path.join(raw, "summary.json"), "w") as f:
        json.dump({**row, "phases": phases}, f, indent=1)
    log("    " + " | ".join(f"{p['phase']}: p99={p['p99_ms']} goodput={p['to_targets_MBps']}/{p['attack_offered_MBps']}MB/s"
                           for p in phases) + f" | flood_ok={row['flood_ok']}")
    return row, phases


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--architectures", nargs="+", default=DEFAULT_ARMS)
    ap.add_argument("--scenarios", nargs="+", default=list(TV), choices=list(TV))
    ap.add_argument("--time-scale", type=float, default=1.0, help="multiply every duration (dry runs: 0.25)")
    ap.add_argument("--qps", type=float, default=20)
    ap.add_argument("--warmup", type=float, default=3.0)
    ap.add_argument("--tenant-cpus", default="1-7")
    ap.add_argument("--controller-cpus", default="0")
    ap.add_argument("--attacker-cpus", default=None)
    ap.add_argument("--rps", action="store_true")
    ap.add_argument("--rps-cpus", default=None)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("must run as root")
    if a.time_scale != 1.0:
        for sc in TV:
            TV[sc] = [(n, max(4, s * a.time_scale), [(max(0.5, d * a.time_scale), x) for d, x in sch], r)
                      for n, s, sch, r in TV[sc]]
    os.sched_setaffinity(0, set(tenants._expand_cpus(str(a.controller_cpus))))
    topo = tenants.load()
    for arch in a.architectures:
        arms.resolve(arch)
    run_dir = a.out or os.path.join(HERE, "results", f"timevarying_N{a.reps}_{topo.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    raw_root = os.path.join(run_dir, "raw")
    os.makedirs(raw_root, exist_ok=True)
    write_env_json(run_dir, a.architectures)
    with open(os.path.join(run_dir, "config.json"), "w") as f:
        json.dump({**vars(a), "tenants_file": topo.path, "scenarios_def": {s: TV[s] for s in a.scenarios},
                   "calibration": os.environ.get("QOS_CALIBRATION")}, f, indent=2)
    trials = [(x, s, r) for x in a.architectures for s in a.scenarios for r in range(1, a.reps + 1)]
    random.Random(a.seed).shuffle(trials)
    log(f"time-varying attacks: {len(trials)} trials, {len(topo.tenants)} tenants, {len(topo.attackers())} attackers -> {run_dir}")
    for t in topo.servers():
        t.cpus = a.tenant_cpus or t.cpus
    secs = topology.up(topo)
    if a.rps:
        log(f"RPS mask {rm.set_rps(topo, a.rps_cpus or a.tenant_cpus)}")
    t0 = time.time()
    n = containers.start(topo)
    log(f"topology {secs:.1f}s, {n} containers {time.time() - t0:.1f}s")
    tcsv, pcsv = os.path.join(run_dir, "results_summary_raw.csv"), os.path.join(run_dir, "phases.csv")
    for path, cols in ((tcsv, TRIAL_COLS), (pcsv, PHASE_COLS)):
        with open(path, "w") as f:
            f.write(",".join(cols) + "\n")
    failures, consecutive = [], 0
    try:
        for i, (arch, s, r) in enumerate(trials, 1):
            log(f"[Trial {i}/{len(trials)}] {arch} | {s} | rep {r}")
            try:
                row, phases = run_trial(topo, arch, s, r, raw_root, a)
            except Exception as ex:  # noqa: BLE001
                import traceback
                log(f"TRIAL FAILED {arch}/{s}/rep {r}: {type(ex).__name__}: {ex}")
                failures.append({"arch": arch, "scenario": s, "rep": r, "error": str(ex), "tb": traceback.format_exc()})
                json.dump(failures, open(os.path.join(run_dir, "failures.json"), "w"), indent=1)
                rm.cleanup_after_failure()
                consecutive += 1
                if consecutive >= 3:
                    raise
                continue
            consecutive = 0
            with open(tcsv, "a") as f:
                f.write(",".join("" if row[c] is None else str(row[c]) for c in TRIAL_COLS) + "\n")
            with open(pcsv, "a") as f:
                for p in phases:
                    f.write(",".join("" if p[c] is None else str(p[c]) for c in PHASE_COLS) + "\n")
    finally:
        sl.stop_floods([])
        containers.stop()
        topology.down(topo)
    log(f"time-varying run complete: {run_dir}")


if __name__ == "__main__":
    main()
