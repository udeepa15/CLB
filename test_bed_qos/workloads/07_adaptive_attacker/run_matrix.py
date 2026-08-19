#!/usr/bin/env python3
"""
run_matrix.py — Workload 07: Rational Adaptive Adversary 3-Way Ablation Runner.

Executes 3-arm benchmark comparison:
1. qos_tiered (Static priority boundary)
2. qos_proportional (Linear proportional feedback control)
3. qos_dynamic (Stackelberg game-theoretic solver)

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
from workload_lib import SHARED_CONFIG, AdaptiveAttackerThread, log, run_cmd, wait_for_port, parse_fortio_json

WORKLOAD_CONFIG = {
    **SHARED_CONFIG,
    "duration_sec": 20
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
    trial_id = f"{arch}_adaptive_rep_{rep}"
    log(f"--- Running Workload 07 Trial: {trial_id} ---")

    # 1. Setup Topology & Attach eBPF (qos_proportional and qos_dynamic both use ebpf_qos_dynamic.c)
    ebpf_target = "qos_dynamic" if arch in ["qos_dynamic", "qos_proportional"] else "qos_tiered"
    run_cmd(f"{os.path.join(base_dir, 'setup_topology.sh')}", cwd=base_dir)
    run_cmd(f"{os.path.join(base_dir, 'attach_ebpf.sh')} {ebpf_target}", cwd=base_dir)

    # 2. Start Controller based on Architecture Arm
    controller_proc = None
    controller_log = os.path.join(raw_dir, f"qos_controller_{trial_id}.jsonl")
    if arch == "qos_dynamic":
        controller_proc = subprocess.Popen(["python3", os.path.join(base_dir, "qos_controller.py"), "--log-file", controller_log, "--interval", "0.2"])
        time.sleep(1)
    elif arch == "qos_proportional":
        prop_controller = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qos_proportional_controller.py")
        controller_proc = subprocess.Popen(["python3", prop_controller, "--log-file", controller_log, "--interval", "0.2"])
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

    # 4. Start Adaptive Attacker Thread
    attacker_thread = AdaptiveAttackerThread(
        target_ip=WORKLOAD_CONFIG["victim1_ip"],
        target_port=WORKLOAD_CONFIG["port_http"],
        controller_log_path=controller_log,
        poll_interval=1.0
    )
    attacker_thread.start()

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

    # 6. Execute Fortio Load
    fortio_json = os.path.join(raw_dir, f"fortio_{trial_id}.json")
    f_cmd = f"taskset -c 0 fortio load -c {WORKLOAD_CONFIG['conns']} -qps {WORKLOAD_CONFIG['qps']} -t {WORKLOAD_CONFIG['duration_sec']}s -json {fortio_json} http://{WORKLOAD_CONFIG['victim1_ip']}:{WORKLOAD_CONFIG['port_http']}/"
    res = subprocess.run(f_cmd, shell=True, capture_output=True, text=True)

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
    return fortio_json

def main():
    parser = argparse.ArgumentParser(description="Workload 07: Rational Adaptive Adversary Runner")
    parser.add_argument("--reps", type=int, default=2, help="Repetitions per cell")
    parser.add_argument("--architectures", nargs="+", default=["qos_tiered", "qos_proportional", "qos_dynamic"], help="3 Arms")
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

    log(f"=== Workload 07: Adaptive Attacker Benchmark (N={args.reps}, Total Trials: {len(trials)}) ===")

    results_raw = []
    for idx, t in enumerate(trials, 1):
        log(f"\n[Trial {idx}/{len(trials)}] Arch: {t['arch']} | Rep: {t['rep']}")
        fortio_json = run_single_trial(t["arch"], t["rep"], base_dir, raw_dir)
        metrics = parse_fortio_json(fortio_json)
        results_raw.append({
            "arch": t["arch"],
            "protocol": "http",
            "pattern": "adaptive_rational_adversary",
            "rep": t["rep"],
            **metrics
        })

    raw_csv = os.path.join(run_dir, "results_summary_raw.csv")
    with open(raw_csv, "w") as f:
        f.write("arch,protocol,pattern,rep,p50_ms,p90_ms,p99_ms,p999_ms,actual_qps\n")
        for r in results_raw:
            f.write(f"{r['arch']},{r['protocol']},{r['pattern']},{r['rep']},{r['p50_ms']},{r['p90_ms']},{r['p99_ms']},{r['p999_ms']},{r['actual_qps']}\n")

    log(f"Workload 07 Benchmark Run Complete! Summaries written to {run_dir}")

if __name__ == "__main__":
    main()
