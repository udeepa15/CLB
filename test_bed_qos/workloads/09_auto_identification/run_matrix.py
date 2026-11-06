#!/usr/bin/env python3
"""
run_matrix.py - Workload 09: automatic attacker/victim identification.

Scenarios (ground truth recorded per trial in raw/truth_<trial>.json):
  single         one flooder (ns_attacker, u20)
  two_attackers  ns_attacker AND ns_victim3 both flood victim1. A hardcoded list treats victim3 as a
                 trusted victim and never caps it; fair-share identification should catch both.
  spoof          ns_attacker floods with hping3 --rand-source (IP keying should fail, port keying hold)
  benign_heavy   NO attacker; victim3 sends heavy-but-legitimate UDP. Any cap here is a false positive.
  heavy_victim   attacker u20 while the victim itself runs 500 QPS (high-demand legitimate tenant)
"""

import os
import sys
import time
import json
import random
import signal
import argparse
import subprocess
from datetime import datetime

sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shared"))
from workload_lib import SHARED_CONFIG, log, run_cmd, wait_for_port, parse_fortio_json, write_env_json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import arms

WORKLOAD_CONFIG = {**SHARED_CONFIG, "duration_sec": 20}

# netns -> (port label, ip label) in the controller's key spaces
IDENT = {"ns_attacker": ("veth-att-br", "10.0.0.20"), "ns_victim3": ("veth-vic3-br", "10.0.0.12")}
# scenario -> (list of (netns, hping3 args), set of truly-malicious netns, fortio qps, fortio conns)
SCENARIOS = {
    "single":        ([("ns_attacker", "-i u20")], {"ns_attacker"}, 50, 2),
    "two_attackers": ([("ns_attacker", "-i u20"), ("ns_victim3", "-i u20")], {"ns_attacker", "ns_victim3"}, 50, 2),
    "spoof":         ([("ns_attacker", "--rand-source -i u20")], {"ns_attacker"}, 50, 2),
    "benign_heavy":  ([("ns_victim3", "-i u100")], set(), 50, 2),
    "heavy_victim":  ([("ns_attacker", "-i u20")], {"ns_attacker"}, 500, 10),
    # Escalating floods: the u20 scenarios above barely hurt the victim even with no protection, so
    # they cannot show a benefit. These push toward line rate (hping3 --flood sends as fast as it can).
    "single_u2":     ([("ns_attacker", "-i u2")], {"ns_attacker"}, 50, 2),
    "single_flood":  ([("ns_attacker", "--flood -d 1000")], {"ns_attacker"}, 50, 2),
    "two_flood":     ([("ns_attacker", "--flood -d 1000"), ("ns_victim3", "--flood -d 1000")], {"ns_attacker", "ns_victim3"}, 50, 2),
}
# The original scenarios (default), vs. the escalating ones used for the unprotected-baseline comparison.
DEFAULT_SCENARIOS = ["single", "two_attackers", "spoof", "benign_heavy", "heavy_victim"]
HARD_SCENARIOS = ["single_u2", "single_flood", "two_flood"]

def prepare_victim_bundle(base_path, victim_id, protocol, port):
    bundle_name = os.path.join(base_path, f"victim_bundle_{victim_id}")
    victim_tmpl = os.path.join(base_path, "victim_bundle")
    run_cmd(f"rm -rf {bundle_name} && cp -r {victim_tmpl} {bundle_name}")

    config_path = os.path.join(bundle_name, "config.json")
    with open(config_path, "r") as f:
        config = json.load(f)

    for ns in config["linux"]["namespaces"]:
        if ns["type"] == "network":
            ns["path"] = f"/var/run/netns/ns_victim{victim_id}"

    if "resources" not in config["linux"]:
        config["linux"]["resources"] = {}
    config["linux"]["resources"]["cpu"] = {"cpus": str(victim_id)}

    config["process"]["args"] = [
        "sh", "-c",
        f"exec python3 /victim_server.py {protocol} {port}"
    ]

    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)


def _rx_packets(port):
    try:
        return int(open(f"/sys/class/net/{port}/statistics/rx_packets").read())
    except OSError:
        return 0


def start_floods(scenario, raw_dir, trial_id, attempts=3, min_pps=300):
    """Start every flood of the scenario and VERIFY each one is alive and delivering packets.

    A flood that silently fails to run invalidates the trial (it then measures 'no attack'), which the
    old launcher could not detect because hping3's output went to /dev/null. hping3 stderr is now kept
    per trial in raw/hping_*.log, the real rate is measured on the sender's own veth, and a failed
    start is retried. Returns (procs, {netns: verified_pps}, launch_epoch_of_first_verified_flood).
    """
    verified, procs, started = {}, [], []
    for ns, hping_args in SCENARIOS[scenario][0]:
        port = IDENT[ns][0]
        for attempt in range(1, attempts + 1):
            logf = open(os.path.join(raw_dir, f"hping_{trial_id}_{ns}_try{attempt}.log"), "w")
            cmd = (f"ip netns exec {ns} hping3 -n --udp -p {WORKLOAD_CONFIG['port_http']} {hping_args} "
                   f"{WORKLOAD_CONFIG['victim1_ip']}")
            t_launch = time.time()
            p = subprocess.Popen(cmd, shell=True, stdout=logf, stderr=subprocess.STDOUT)
            time.sleep(1.0)
            r0 = _rx_packets(port); time.sleep(0.5); r1 = _rx_packets(port)
            pps = (r1 - r0) / 0.5
            if p.poll() is None and pps >= min_pps:
                procs.append(p); verified[ns] = pps; started.append(t_launch)
                break
            log(f"WARNING: flood on {ns} not delivering (alive={p.poll() is None}, {pps:.0f} pps), attempt {attempt}/{attempts}")
            p.kill(); run_cmd(f"pkill -9 -f 'netns exec {ns} hping3' 2>/dev/null || true", check=False)
            time.sleep(0.5)
        else:
            verified[ns] = 0.0
    return procs, verified, (min(started) if started else time.time())

def run_single_trial(arch, scenario, rep, base_dir, raw_dir):
    trial_id = f"{arch}_{scenario}_rep_{rep}"
    log(f"--- Running Workload 09 Trial: {trial_id} ---")

    run_cmd(f"{os.path.join(base_dir, 'setup_topology.sh')}", cwd=base_dir)
    arms.attach(arch, base_dir)
    controller_proc = arms.start_controller(arch, os.path.join(raw_dir, f"qos_controller_{trial_id}.jsonl"), base_dir)

    run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
    run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)
    time.sleep(1)
    prepare_victim_bundle(base_dir, 1, "http", WORKLOAD_CONFIG["port_http"])
    bundle_path = os.path.join(base_dir, "victim_bundle_1")
    r_res = run_cmd(f"runc run --bundle {bundle_path} -d victim_container_1 >/dev/null 2>&1", check=False)
    if r_res.returncode != 0:
        log(f"CRITICAL ERROR: Container run failed: {r_res.stderr}")
        if controller_proc: controller_proc.kill()
        sys.exit(1)
    if not wait_for_port(WORKLOAD_CONFIG["victim1_ip"], WORKLOAD_CONFIG["port_http"]):
        if controller_proc: controller_proc.kill()
        sys.exit(1)

    attackers = SCENARIOS[scenario][1]
    flood_procs, flood_pps, start_epoch = start_floods(scenario, raw_dir, trial_id)
    with open(os.path.join(raw_dir, f"truth_{trial_id}.json"), "w") as f:
        json.dump({"scenario": scenario, "start_epoch": start_epoch,
                   "attacker_port_labels": sorted(IDENT[n][0] for n in attackers),
                   "attacker_ip_labels": [] if scenario == "spoof" else sorted(IDENT[n][1] for n in attackers),
                   "spoofed_sources": scenario == "spoof",
                   "flood_verified_pps": flood_pps,
                   "flood_ok": all(v >= 300 for v in flood_pps.values())}, f, indent=2)

    ebpf_log = os.path.join(raw_dir, f"ebpf_stats_{trial_id}.jsonl")
    ebpf_poller = subprocess.Popen(["python3", os.path.join(base_dir, "collect_ebpf_stats.py"), ebpf_log])
    time.sleep(WORKLOAD_CONFIG["warmup_sec"])

    qps, conns = SCENARIOS[scenario][2], SCENARIOS[scenario][3]
    fortio_json = os.path.join(raw_dir, f"fortio_{trial_id}.json")
    f_cmd = (f"taskset -c 0 fortio load -c {conns} -qps {qps} -t {WORKLOAD_CONFIG['duration_sec']}s -json {fortio_json} "
             f"http://{WORKLOAD_CONFIG['victim1_ip']}:{WORKLOAD_CONFIG['port_http']}/")
    res = subprocess.run(f_cmd, shell=True, capture_output=True, text=True)

    ebpf_poller.send_signal(signal.SIGINT)
    if controller_proc: controller_proc.kill()
    for p in flood_procs: p.kill()
    run_cmd("pkill -9 hping3 2>/dev/null || true", check=False)
    run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
    run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)
    if res.returncode != 0:
        log(f"CRITICAL ERROR: Fortio run failed for trial {trial_id}!")
        sys.exit(1)
    time.sleep(1.0)
    return fortio_json

def main():
    parser = argparse.ArgumentParser(description="Workload 09: automatic identification runner")
    parser.add_argument("--reps", type=int, default=2)
    parser.add_argument("--architectures", nargs="+", default=["qos_tiered"] + list(arms.AUTO_ARMS))
    parser.add_argument("--scenarios", nargs="+", default=DEFAULT_SCENARIOS, choices=list(SCENARIOS))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if os.geteuid() != 0:
        log("CRITICAL ERROR: Must be run as root (sudo python3 run_matrix.py)")
        sys.exit(1)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    base_dir = os.path.dirname(os.path.dirname(script_dir))
    run_dir = os.path.join(script_dir, "results", f"multi_run_N{args.reps}_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    raw_dir = os.path.join(run_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    write_env_json(run_dir, args.architectures)

    trials = [{"arch": a, "scenario": s, "rep": r} for a in args.architectures for s in args.scenarios
              for r in range(1, args.reps + 1)]
    random.seed(args.seed); random.shuffle(trials)
    log(f"=== Workload 09: Automatic Identification (N={args.reps}, Total Trials: {len(trials)}) ===")

    rows = []
    for idx, t in enumerate(trials, 1):
        log(f"\n[Trial {idx}/{len(trials)}] Arch: {t['arch']} | Scenario: {t['scenario']} | Rep: {t['rep']}")
        m = parse_fortio_json(run_single_trial(t["arch"], t["scenario"], t["rep"], base_dir, raw_dir))
        rows.append({**t, "protocol": "http", **m})

    with open(os.path.join(run_dir, "results_summary_raw.csv"), "w") as f:
        f.write("arch,scenario,protocol,rep,p50_ms,p90_ms,p99_ms,p999_ms,actual_qps\n")
        for r in rows:
            f.write(f"{r['arch']},{r['scenario']},{r['protocol']},{r['rep']},{r['p50_ms']},{r['p90_ms']},{r['p99_ms']},{r['p999_ms']},{r['actual_qps']}\n")
    log(f"Workload 09 Run Complete! Summaries written to {run_dir}")

if __name__ == "__main__":
    main()
