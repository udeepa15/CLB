#!/usr/bin/env python3
"""
run_matrix.py — Workload 04: Flash Crowd Victim Demand Surge Runner.

Executes trials where attacker flood is constant (u20) and victim QPS surges
from 50 QPS (10s) to 500 QPS (10s).

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
from workload_lib import SHARED_CONFIG, log, run_cmd, wait_for_port, parse_fortio_json

WORKLOAD_CONFIG = {
    **SHARED_CONFIG,
    "constant_flood": "u20",
    "phase1_qps": 50,
    "phase1_dur": 10,
    "phase2_qps": 500,
    "phase2_dur": 10
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
    trial_id = f"{arch}_flashcrowd_rep_{rep}"
    log(f"--- Running Workload 04 Trial: {trial_id} ---")

    # 1. Setup Topology & Attach eBPF
    run_cmd(f"{os.path.join(base_dir, 'setup_topology.sh')}", cwd=base_dir)
    run_cmd(f"{os.path.join(base_dir, 'attach_ebpf.sh')} {arch}", cwd=base_dir)

    # 2. Start Stackelberg Controller if qos_dynamic
    controller_proc = None
    if arch == "qos_dynamic":
        controller_log = os.path.join(raw_dir, f"qos_controller_{trial_id}.jsonl")
        controller_proc = subprocess.Popen(["python3", os.path.join(base_dir, "qos_controller.py"), "--log-file", controller_log, "--interval", "0.2"])
        time.sleep(1)

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

    # 4. Start Constant Attacker Flood (u20)
    cmd_att = f"ip netns exec ns_attacker hping3 --udp -p {WORKLOAD_CONFIG['port_http']} -i {WORKLOAD_CONFIG['constant_flood']} {WORKLOAD_CONFIG['victim1_ip']}"
    attacker_proc = subprocess.Popen(cmd_att, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)

    # 5. Start Instrumentation Pollers
    ebpf_log = os.path.join(raw_dir, f"ebpf_stats_{trial_id}.jsonl")

    ebpf_poller = subprocess.Popen(["python3", os.path.join(base_dir, "collect_ebpf_stats.py"), ebpf_log])
    net_poller = subprocess.Popen([
        os.path.join(base_dir, "collect_network_stats.sh"),
        "br-mesh",
        raw_dir,
        trial_id
    ])

    time.sleep(WORKLOAD_CONFIG["warmup_sec"])

    # 6. Execute Phase 1: 50 QPS for 10s
    log("Executing Phase 1 (50 QPS Baseline)...")
    fortio_json1 = os.path.join(raw_dir, f"fortio_phase1_{trial_id}.json")
    f_cmd1 = f"taskset -c 0 fortio load -c {WORKLOAD_CONFIG['conns']} -qps {WORKLOAD_CONFIG['phase1_qps']} -t {WORKLOAD_CONFIG['phase1_dur']}s -json {fortio_json1} http://{WORKLOAD_CONFIG['victim1_ip']}:{WORKLOAD_CONFIG['port_http']}/"
    res1 = subprocess.run(f_cmd1, shell=True, capture_output=True, text=True)

    # 7. Execute Phase 2: 500 QPS Flash Crowd Surge for 10s
    log("Executing Phase 2 (500 QPS Flash Crowd Surge)...")
    fortio_json2 = os.path.join(raw_dir, f"fortio_phase2_{trial_id}.json")
    f_cmd2 = f"taskset -c 0 fortio load -c 10 -qps {WORKLOAD_CONFIG['phase2_qps']} -t {WORKLOAD_CONFIG['phase2_dur']}s -json {fortio_json2} http://{WORKLOAD_CONFIG['victim1_ip']}:{WORKLOAD_CONFIG['port_http']}/"
    res2 = subprocess.run(f_cmd2, shell=True, capture_output=True, text=True)

    # Cleanup
    ebpf_poller.send_signal(signal.SIGINT)
    net_poller.send_signal(signal.SIGINT)
    attacker_proc.kill()
    run_cmd("pkill -9 hping3 2>/dev/null || true", check=False)
    if controller_proc: controller_proc.kill()

    run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
    run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)

    if res1.returncode != 0 or res2.returncode != 0:
        log(f"CRITICAL ERROR: Fortio run failed for trial {trial_id}!")
        sys.exit(1)

    time.sleep(1.0)
    return fortio_json1, fortio_json2

def main():
    parser = argparse.ArgumentParser(description="Workload 04: Flash Crowd Victim Surge Runner")
    parser.add_argument("--reps", type=int, default=2, help="Repetitions per cell")
    parser.add_argument("--architectures", nargs="+", default=["qos_tiered", "qos_dynamic"], help="Architectures to test")
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

    trials = []
    for arch in args.architectures:
        for rep in range(1, args.reps + 1):
            trials.append({"arch": arch, "rep": rep})

    random.seed(args.seed)
    random.shuffle(trials)

    log(f"=== Workload 04: Flash Crowd Benchmark (N={args.reps}, Total Trials: {len(trials)}) ===")

    results_raw = []
    for idx, t in enumerate(trials, 1):
        log(f"\n[Trial {idx}/{len(trials)}] Arch: {t['arch']} | Rep: {t['rep']}")
        json1, json2 = run_single_trial(t["arch"], t["rep"], base_dir, raw_dir)
        m1 = parse_fortio_json(json1)
        m2 = parse_fortio_json(json2)
        results_raw.append({
            "arch": t["arch"],
            "protocol": "http",
            "rep": t["rep"],
            "phase1_p99_ms": m1["p99_ms"],
            "phase1_qps": m1["actual_qps"],
            "phase2_p99_ms": m2["p99_ms"],
            "phase2_qps": m2["actual_qps"]
        })

    raw_csv = os.path.join(run_dir, "results_summary_raw.csv")
    with open(raw_csv, "w") as f:
        f.write("arch,protocol,rep,phase1_p99_ms,phase1_qps,phase2_p99_ms,phase2_qps\n")
        for r in results_raw:
            f.write(f"{r['arch']},{r['protocol']},{r['rep']},{r['phase1_p99_ms']},{r['phase1_qps']},{r['phase2_p99_ms']},{r['phase2_qps']}\n")

    log(f"Workload 04 Benchmark Run Complete! Summaries written to {run_dir}")

if __name__ == "__main__":
    main()
