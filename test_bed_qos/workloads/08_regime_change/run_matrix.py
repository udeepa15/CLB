#!/usr/bin/env python3
"""
run_matrix.py — Workload 02: Bursty Oscillating Attacker Traffic Runner.

Executes trials where the attacker alternates between low (u200) and high (flood)
intensity every 5s during a 20s run.

Reuses test_bed_qos core scripts and shared workload_lib.py.
"""

import os
import sys
import time
import json
import random
import signal
import socket
import argparse
import subprocess
from datetime import datetime

sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shared"))
from workload_lib import SHARED_CONFIG, RegimeChangeAttackerThread, log, run_cmd, wait_for_port, parse_fortio_json, write_env_json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import arms

WORKLOAD_CONFIG = {
    **SHARED_CONFIG,
    "phase_a_dur": 30,          # calm: constant u200 (~5k pps)
    "phase_b_dur": 30,          # sawtooth: flood 2s / u200 2s
    "level_a": "u200",
    "level_b1": "flood",
    "level_b2": "u200",
    "phase_b_interval_sec": 2.0
}

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

def run_single_trial(arch, rep, base_dir, raw_dir):
    trial_id = f"{arch}_bursty_rep_{rep}"
    log(f"--- Running Workload 02 Trial: {trial_id} ---")

    # 1. Setup Topology & Attach eBPF
    run_cmd(f"{os.path.join(base_dir, 'setup_topology.sh')}", cwd=base_dir)
    arms.attach(arch, base_dir)

    # 2. Start Stackelberg Controller if qos_dynamic
    controller_proc = arms.start_controller(arch, os.path.join(raw_dir, f"qos_controller_{trial_id}.jsonl"), base_dir)

    # 3. Start Victim Container
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

    # 4. Start Bursty Oscillating Attacker Thread
    # attacker starts before the warmup, so lengthen phase A by the warmup to align the regime
    # switch with the boundary between the two fortio phases
    attacker_thread = RegimeChangeAttackerThread(
        target_ip=WORKLOAD_CONFIG["victim1_ip"],
        target_port=WORKLOAD_CONFIG["port_http"],
        level_a=WORKLOAD_CONFIG["level_a"],
        phase_a_sec=WORKLOAD_CONFIG["phase_a_dur"] + WORKLOAD_CONFIG["warmup_sec"],
        level_b1=WORKLOAD_CONFIG["level_b1"],
        level_b2=WORKLOAD_CONFIG["level_b2"],
        phase_b_interval_sec=WORKLOAD_CONFIG["phase_b_interval_sec"]
    )
    attacker_thread.start()

    # 5. Start Instrumentation Pollers
    ebpf_log = os.path.join(raw_dir, f"ebpf_stats_{trial_id}.jsonl")
    net_log = os.path.join(raw_dir, f"network_{trial_id}.jsonl")

    ebpf_poller = subprocess.Popen(["python3", os.path.join(base_dir, "collect_ebpf_stats.py"), ebpf_log])
    net_poller = subprocess.Popen([
        os.path.join(base_dir, "collect_network_stats.sh"),
        "br-mesh",
        raw_dir,
        trial_id
    ])

    time.sleep(WORKLOAD_CONFIG["warmup_sec"])

    # 6. Execute Fortio Load in two consecutive phases (A: calm, B: after regime change)
    url = f"http://{WORKLOAD_CONFIG['victim1_ip']}:{WORKLOAD_CONFIG['port_http']}/"
    fortio_a = os.path.join(raw_dir, f"fortio_phaseA_{trial_id}.json")
    fortio_b = os.path.join(raw_dir, f"fortio_phaseB_{trial_id}.json")
    res = subprocess.run(f"taskset -c 0 fortio load -c {WORKLOAD_CONFIG['conns']} -qps {WORKLOAD_CONFIG['qps']} -t {WORKLOAD_CONFIG['phase_a_dur']}s -json {fortio_a} {url}",
                         shell=True, capture_output=True, text=True)
    if res.returncode == 0:
        res = subprocess.run(f"taskset -c 0 fortio load -c {WORKLOAD_CONFIG['conns']} -qps {WORKLOAD_CONFIG['qps']} -t {WORKLOAD_CONFIG['phase_b_dur']}s -json {fortio_b} {url}",
                             shell=True, capture_output=True, text=True)

    # Cleanup
    ebpf_poller.send_signal(signal.SIGINT)
    net_poller.send_signal(signal.SIGINT)
    attacker_thread.stop()
    if controller_proc: controller_proc.kill()

    run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
    run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)

    if res.returncode != 0:
        log(f"CRITICAL ERROR: Fortio run failed for trial {trial_id}!")
        sys.exit(1)

    time.sleep(1.0)
    return fortio_a, fortio_b

def main():
    parser = argparse.ArgumentParser(description="Workload 08: Attacker Regime Change Runner")
    parser.add_argument("--reps", type=int, default=2, help="Repetitions per cell")
    parser.add_argument("--architectures", nargs="+", default=list(arms.FORGET_ARMS), help="Architectures to test")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    if os.geteuid() != 0:
        log("CRITICAL ERROR: Must be run as root (sudo python3 run_matrix.py)")
        sys.exit(1)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    base_dir = os.path.dirname(os.path.dirname(script_dir))

    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(script_dir, "results", f"multi_run_N{args.reps}_{ts_str}")
    raw_dir = os.path.join(run_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    write_env_json(run_dir, args.architectures)

    trials = []
    for arch in args.architectures:
        for rep in range(1, args.reps + 1):
            trials.append({"arch": arch, "rep": rep})

    random.seed(args.seed)
    random.shuffle(trials)

    log(f"=== Workload 08: Regime Change Benchmark (N={args.reps}, Total Trials: {len(trials)}) ===")

    results_raw = []
    for idx, t in enumerate(trials, 1):
        log(f"\n[Trial {idx}/{len(trials)}] Arch: {t['arch']} | Rep: {t['rep']}")
        fa, fb = run_single_trial(t["arch"], t["rep"], base_dir, raw_dir)
        ma, mb = parse_fortio_json(fa), parse_fortio_json(fb)
        results_raw.append({
            "arch": t["arch"],
            "protocol": "http",
            "rep": t["rep"],
            "phaseA_p99_ms": ma["p99_ms"], "phaseA_qps": ma["actual_qps"],
            "phaseB_p99_ms": mb["p99_ms"], "phaseB_qps": mb["actual_qps"],
            "phaseB_p999_ms": mb["p999_ms"],
        })

    raw_csv = os.path.join(run_dir, "results_summary_raw.csv")
    with open(raw_csv, "w") as f:
        f.write("arch,protocol,rep,phaseA_p99_ms,phaseA_qps,phaseB_p99_ms,phaseB_qps,phaseB_p999_ms\n")
        for r in results_raw:
            f.write(f"{r['arch']},{r['protocol']},{r['rep']},{r['phaseA_p99_ms']},{r['phaseA_qps']},{r['phaseB_p99_ms']},{r['phaseB_qps']},{r['phaseB_p999_ms']}\n")

    log(f"Workload 08 Benchmark Run Complete! Summaries written to {run_dir}")

if __name__ == "__main__":
    main()
