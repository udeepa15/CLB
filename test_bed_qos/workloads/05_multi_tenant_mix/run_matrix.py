#!/usr/bin/env python3
"""
run_matrix.py — Workload 05: Heterogeneous Multi-Tenant Co-location Mix Runner.

Executes trials co-locating Victim 1 (50 QPS real-time) and Victim 2 (200 QPS background)
under simultaneous multi-target flood noise.

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
    "duration_sec": 10,
    "v1_qps": 50,
    "v2_qps": 200,
    "att_v1_flood": "u20",
    "att_v2_flood": "u200"
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
    trial_id = f"{arch}_multitenant_rep_{rep}"
    log(f"--- Running Workload 05 Trial: {trial_id} ---")

    # 1. Setup Topology & Attach eBPF
    run_cmd(f"{os.path.join(base_dir, 'setup_topology.sh')}", cwd=base_dir)
    run_cmd(f"{os.path.join(base_dir, 'attach_ebpf.sh')} {arch}", cwd=base_dir)

    # 2. Start Stackelberg Controller if qos_dynamic
    controller_proc = None
    if arch == "qos_dynamic":
        controller_log = os.path.join(raw_dir, f"qos_controller_{trial_id}.jsonl")
        controller_proc = subprocess.Popen(["python3", os.path.join(base_dir, "qos_controller.py"), "--log-file", controller_log, "--interval", "0.2"])
        time.sleep(1)

    # 3. Start Victim Containers (Victim 1 and Victim 2)
    for v_id in [1, 2]:
        run_cmd(f"runc kill victim_container_{v_id} KILL 2>/dev/null || true", check=False)
        run_cmd(f"runc delete victim_container_{v_id} 2>/dev/null || true", check=False)
        time.sleep(0.5)
        prepare_victim_bundle(base_dir, v_id, "http", WORKLOAD_CONFIG["port_http"])
        bundle_path = os.path.join(base_dir, f"victim_bundle_{v_id}")
        r_res = run_cmd(f"runc run --bundle {bundle_path} -d victim_container_{v_id} >/dev/null 2>&1", check=False)
        if r_res.returncode != 0:
            log(f"CRITICAL ERROR: Container victim_container_{v_id} run failed!")
            if controller_proc: controller_proc.kill()
            sys.exit(1)

    if not wait_for_port(WORKLOAD_CONFIG["victim1_ip"], WORKLOAD_CONFIG["port_http"]) or \
       not wait_for_port(WORKLOAD_CONFIG["victim2_ip"], WORKLOAD_CONFIG["port_http"]):
        if controller_proc: controller_proc.kill()
        sys.exit(1)

    # 4. Start Dual Attacker Floods
    cmd_att1 = f"ip netns exec ns_attacker hping3 --udp -p {WORKLOAD_CONFIG['port_http']} -i {WORKLOAD_CONFIG['att_v1_flood']} {WORKLOAD_CONFIG['victim1_ip']}"
    cmd_att2 = f"ip netns exec ns_attacker hping3 --udp -p {WORKLOAD_CONFIG['port_http']} -i {WORKLOAD_CONFIG['att_v2_flood']} {WORKLOAD_CONFIG['victim2_ip']}"
    att_proc1 = subprocess.Popen(cmd_att1, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    att_proc2 = subprocess.Popen(cmd_att2, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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

    # 6. Execute Parallel Fortio Loads (Victim 1: 50 QPS, Victim 2: 200 QPS)
    fortio_v1_json = os.path.join(raw_dir, f"fortio_v1_{trial_id}.json")
    fortio_v2_json = os.path.join(raw_dir, f"fortio_v2_{trial_id}.json")

    f_cmd1 = f"taskset -c 0 fortio load -c 2 -qps {WORKLOAD_CONFIG['v1_qps']} -t {WORKLOAD_CONFIG['duration_sec']}s -json {fortio_v1_json} http://{WORKLOAD_CONFIG['victim1_ip']}:{WORKLOAD_CONFIG['port_http']}/"
    f_cmd2 = f"taskset -c 1 fortio load -c 4 -qps {WORKLOAD_CONFIG['v2_qps']} -t {WORKLOAD_CONFIG['duration_sec']}s -json {fortio_v2_json} http://{WORKLOAD_CONFIG['victim2_ip']}:{WORKLOAD_CONFIG['port_http']}/"

    p1 = subprocess.Popen(f_cmd1, shell=True)
    p2 = subprocess.Popen(f_cmd2, shell=True)

    p1.wait()
    p2.wait()

    # Cleanup
    ebpf_poller.send_signal(signal.SIGINT)
    net_poller.send_signal(signal.SIGINT)
    att_proc1.kill()
    att_proc2.kill()
    run_cmd("pkill -9 hping3 2>/dev/null || true", check=False)
    if controller_proc: controller_proc.kill()

    for v_id in [1, 2]:
        run_cmd(f"runc kill victim_container_{v_id} KILL 2>/dev/null || true", check=False)
        run_cmd(f"runc delete victim_container_{v_id} 2>/dev/null || true", check=False)

    time.sleep(1.0)
    return fortio_v1_json, fortio_v2_json

def main():
    parser = argparse.ArgumentParser(description="Workload 05: Multi-Tenant Mix Runner")
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

    log(f"=== Workload 05: Multi-Tenant Benchmark (N={args.reps}, Total Trials: {len(trials)}) ===")

    results_raw = []
    for idx, t in enumerate(trials, 1):
        log(f"\n[Trial {idx}/{len(trials)}] Arch: {t['arch']} | Rep: {t['rep']}")
        v1_json, v2_json = run_single_trial(t["arch"], t["rep"], base_dir, raw_dir)
        m1 = parse_fortio_json(v1_json)
        m2 = parse_fortio_json(v2_json)
        results_raw.append({
            "arch": t["arch"],
            "protocol": "http",
            "rep": t["rep"],
            "v1_p99_ms": m1["p99_ms"],
            "v1_qps": m1["actual_qps"],
            "v2_p99_ms": m2["p99_ms"],
            "v2_qps": m2["actual_qps"]
        })

    raw_csv = os.path.join(run_dir, "results_summary_raw.csv")
    with open(raw_csv, "w") as f:
        f.write("arch,protocol,rep,v1_p99_ms,v1_qps,v2_p99_ms,v2_qps\n")
        for r in results_raw:
            f.write(f"{r['arch']},{r['protocol']},{r['rep']},{r['v1_p99_ms']},{r['v1_qps']},{r['v2_p99_ms']},{r['v2_qps']}\n")

    log(f"Workload 05 Benchmark Run Complete! Summaries written to {run_dir}")

if __name__ == "__main__":
    main()
