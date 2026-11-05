#!/usr/bin/env python3
"""
run_markov_runner.py — Standalone Runner for qos_markov across Workloads 01..06.

Executes Workloads 01 to 06 specifically for the qos_markov architectural arm.
All result JSONs, CSV summaries, and controller JSONL logs are stored exclusively in
~/CLB/test_bed_qos/markov_experiments/results/.

Usage:
    sudo python3 run_markov_runner.py --reps 10
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

# Add shared library from workloads directory
CLB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(CLB_DIR, "workloads", "shared"))
from workload_lib import SHARED_CONFIG, BurstyAttackerThread, RampingAttackerThread, log, run_cmd, wait_for_port, parse_fortio_json

MARKOV_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(MARKOV_DIR, "results")
CONTROLLER_SCRIPT = os.path.join(MARKOV_DIR, "qos_controller_markov.py")


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


def start_hping(level, target_ip="10.0.0.10", port=8080):
    if level == "0":
        return None
    if level == "flood":
        cmd = f"ip netns exec ns_attacker hping3 --udp -p {port} --flood {target_ip}"
    else:
        cmd = f"ip netns exec ns_attacker hping3 --udp -p {port} -i {level} {target_ip}"
    proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.5)
    return proc


def run_workload_01(reps):
    """Workload 01: Steady State Baseline across flood levels."""
    log("\n=== Executing Workload 01 (Steady State) for qos_markov ===")
    wl_dir = os.path.join(RESULTS_DIR, "workload_01")
    raw_dir = os.path.join(wl_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    flood_levels = ["0", "u200", "u20", "u2", "u1", "flood"]
    results = []

    for fl in flood_levels:
        for rep in range(1, reps + 1):
            trial_id = f"qos_markov_flood_{fl}_rep_{rep}"
            log(f"[Workload 01] Trial {trial_id}")

            run_cmd(f"{os.path.join(CLB_DIR, 'setup_topology.sh')}", cwd=CLB_DIR)
            run_cmd(f"{os.path.join(CLB_DIR, 'attach_ebpf.sh')} qos_dynamic", cwd=CLB_DIR)

            ctrl_log = os.path.join(raw_dir, f"qos_controller_{trial_id}.jsonl")
            ctrl_proc = subprocess.Popen(["python3", CONTROLLER_SCRIPT, "--log-file", ctrl_log, "--interval", "0.1"])
            time.sleep(1)

            run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
            run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)
            prepare_victim_bundle(CLB_DIR, 1, "http", 8080)
            run_cmd(f"runc run --bundle {os.path.join(CLB_DIR, 'victim_bundle_1')} -d victim_container_1 >/dev/null 2>&1", check=False)

            if not wait_for_port("10.0.0.10", 8080):
                ctrl_proc.kill()
                continue

            att_proc = start_hping(fl)
            time.sleep(2.0)

            out_json = os.path.join(raw_dir, f"fortio_{trial_id}.json")
            f_cmd = f"taskset -c 0 fortio load -c 2 -qps 50 -t 10s -json {out_json} http://10.0.0.10:8080/"
            subprocess.run(f_cmd, shell=True, capture_output=True, text=True)

            if att_proc:
                att_proc.kill()
                run_cmd("pkill -9 hping3 2>/dev/null || true", check=False)
            ctrl_proc.kill()
            run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
            run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)

            metrics = parse_fortio_json(out_json)
            results.append({"arch": "qos_markov", "flood_level": fl, "rep": rep, **metrics})

    raw_csv = os.path.join(wl_dir, "results_summary_raw.csv")
    with open(raw_csv, "w") as f:
        f.write("arch,protocol,flood_level,rep,p50_ms,p90_ms,p99_ms,p999_ms,actual_qps\n")
        for r in results:
            f.write(f"{r['arch']},http,{r['flood_level']},{r['rep']},{r['p50_ms']},{r['p90_ms']},{r['p99_ms']},{r['p999_ms']},{r['actual_qps']}\n")


def run_workload_02(reps):
    """Workload 02: Bursty Oscillating Attacker."""
    log("\n=== Executing Workload 02 (Bursty Oscillating) for qos_markov ===")
    wl_dir = os.path.join(RESULTS_DIR, "workload_02")
    raw_dir = os.path.join(wl_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    results = []
    for rep in range(1, reps + 1):
        trial_id = f"qos_markov_bursty_rep_{rep}"
        log(f"[Workload 02] Trial {trial_id}")

        run_cmd(f"{os.path.join(CLB_DIR, 'setup_topology.sh')}", cwd=CLB_DIR)
        run_cmd(f"{os.path.join(CLB_DIR, 'attach_ebpf.sh')} qos_dynamic", cwd=CLB_DIR)

        ctrl_log = os.path.join(raw_dir, f"qos_controller_{trial_id}.jsonl")
        ctrl_proc = subprocess.Popen(["python3", CONTROLLER_SCRIPT, "--log-file", ctrl_log, "--interval", "0.1"])
        time.sleep(1)

        run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
        run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)
        prepare_victim_bundle(CLB_DIR, 1, "http", 8080)
        run_cmd(f"runc run --bundle {os.path.join(CLB_DIR, 'victim_bundle_1')} -d victim_container_1 >/dev/null 2>&1", check=False)

        if not wait_for_port("10.0.0.10", 8080):
            ctrl_proc.kill()
            continue

        att_thread = BurstyAttackerThread("10.0.0.10", 8080, level1="u200", level2="flood", interval_sec=5.0)
        att_thread.start()
        time.sleep(2.0)

        out_json = os.path.join(raw_dir, f"fortio_{trial_id}.json")
        f_cmd = f"taskset -c 0 fortio load -c 2 -qps 50 -t 20s -json {out_json} http://10.0.0.10:8080/"
        subprocess.run(f_cmd, shell=True, capture_output=True, text=True)

        att_thread.stop()
        ctrl_proc.kill()
        run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
        run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)

        metrics = parse_fortio_json(out_json)
        results.append({"arch": "qos_markov", "pattern": "bursty_u200_flood", "rep": rep, **metrics})

    raw_csv = os.path.join(wl_dir, "results_summary_raw.csv")
    with open(raw_csv, "w") as f:
        f.write("arch,protocol,pattern,rep,p50_ms,p90_ms,p99_ms,p999_ms,actual_qps\n")
        for r in results:
            f.write(f"{r['arch']},http,{r['pattern']},{r['rep']},{r['p50_ms']},{r['p90_ms']},{r['p99_ms']},{r['p999_ms']},{r['actual_qps']}\n")


def run_workload_03(reps):
    """Workload 03: Ramping Escalation Attacker."""
    log("\n=== Executing Workload 03 (Ramping Escalation) for qos_markov ===")
    wl_dir = os.path.join(RESULTS_DIR, "workload_03")
    raw_dir = os.path.join(wl_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    results = []
    for rep in range(1, reps + 1):
        trial_id = f"qos_markov_ramping_rep_{rep}"
        log(f"[Workload 03] Trial {trial_id}")

        run_cmd(f"{os.path.join(CLB_DIR, 'setup_topology.sh')}", cwd=CLB_DIR)
        run_cmd(f"{os.path.join(CLB_DIR, 'attach_ebpf.sh')} qos_dynamic", cwd=CLB_DIR)

        ctrl_log = os.path.join(raw_dir, f"qos_controller_{trial_id}.jsonl")
        ctrl_proc = subprocess.Popen(["python3", CONTROLLER_SCRIPT, "--log-file", ctrl_log, "--interval", "0.1"])
        time.sleep(1)

        run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
        run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)
        prepare_victim_bundle(CLB_DIR, 1, "http", 8080)
        run_cmd(f"runc run --bundle {os.path.join(CLB_DIR, 'victim_bundle_1')} -d victim_container_1 >/dev/null 2>&1", check=False)

        if not wait_for_port("10.0.0.10", 8080):
            ctrl_proc.kill()
            continue

        att_thread = RampingAttackerThread("10.0.0.10", 8080, step_sec=2.0)
        att_thread.start()
        time.sleep(2.0)

        out_json = os.path.join(raw_dir, f"fortio_{trial_id}.json")
        f_cmd = f"taskset -c 0 fortio load -c 2 -qps 50 -t 16s -json {out_json} http://10.0.0.10:8080/"
        subprocess.run(f_cmd, shell=True, capture_output=True, text=True)

        att_thread.stop()
        ctrl_proc.kill()
        run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
        run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)

        metrics = parse_fortio_json(out_json)
        results.append({"arch": "qos_markov", "pattern": "ramping_step_escalation", "rep": rep, **metrics})

    raw_csv = os.path.join(wl_dir, "results_summary_raw.csv")
    with open(raw_csv, "w") as f:
        f.write("arch,protocol,pattern,rep,p50_ms,p90_ms,p99_ms,p999_ms,actual_qps\n")
        for r in results:
            f.write(f"{r['arch']},http,{r['pattern']},{r['rep']},{r['p50_ms']},{r['p90_ms']},{r['p99_ms']},{r['p999_ms']},{r['actual_qps']}\n")


def run_workload_04(reps):
    """Workload 04: Flash Crowd Victim Demand Surge."""
    log("\n=== Executing Workload 04 (Flash Crowd Victim) for qos_markov ===")
    wl_dir = os.path.join(RESULTS_DIR, "workload_04")
    raw_dir = os.path.join(wl_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    results = []
    for rep in range(1, reps + 1):
        trial_id = f"qos_markov_flashcrowd_rep_{rep}"
        log(f"[Workload 04] Trial {trial_id}")

        run_cmd(f"{os.path.join(CLB_DIR, 'setup_topology.sh')}", cwd=CLB_DIR)
        run_cmd(f"{os.path.join(CLB_DIR, 'attach_ebpf.sh')} qos_dynamic", cwd=CLB_DIR)

        ctrl_log = os.path.join(raw_dir, f"qos_controller_{trial_id}.jsonl")
        ctrl_proc = subprocess.Popen(["python3", CONTROLLER_SCRIPT, "--log-file", ctrl_log, "--interval", "0.1"])
        time.sleep(1)

        run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
        run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)
        prepare_victim_bundle(CLB_DIR, 1, "http", 8080)
        run_cmd(f"runc run --bundle {os.path.join(CLB_DIR, 'victim_bundle_1')} -d victim_container_1 >/dev/null 2>&1", check=False)

        if not wait_for_port("10.0.0.10", 8080):
            ctrl_proc.kill()
            continue

        att_proc = start_hping("u20")
        time.sleep(2.0)

        # Phase 1: Normal load (50 QPS, 10s)
        out_json_p1 = os.path.join(raw_dir, f"fortio_{trial_id}_p1.json")
        f_cmd_p1 = f"taskset -c 0 fortio load -c 2 -qps 50 -t 10s -json {out_json_p1} http://10.0.0.10:8080/"
        subprocess.run(f_cmd_p1, shell=True, capture_output=True, text=True)

        # Phase 2: Flash crowd surge (500 QPS, 10s)
        out_json_p2 = os.path.join(raw_dir, f"fortio_{trial_id}_p2.json")
        f_cmd_p2 = f"taskset -c 0 fortio load -c 8 -qps 500 -t 10s -json {out_json_p2} http://10.0.0.10:8080/"
        subprocess.run(f_cmd_p2, shell=True, capture_output=True, text=True)

        if att_proc:
            att_proc.kill()
            run_cmd("pkill -9 hping3 2>/dev/null || true", check=False)
        ctrl_proc.kill()
        run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
        run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)

        m1 = parse_fortio_json(out_json_p1)
        m2 = parse_fortio_json(out_json_p2)
        results.append({"arch": "qos_markov", "phase": "phase1_normal", "rep": rep, **m1})
        results.append({"arch": "qos_markov", "phase": "phase2_surge", "rep": rep, **m2})

    raw_csv = os.path.join(wl_dir, "results_summary_raw.csv")
    with open(raw_csv, "w") as f:
        f.write("arch,protocol,phase,rep,p50_ms,p90_ms,p99_ms,p999_ms,actual_qps\n")
        for r in results:
            f.write(f"{r['arch']},http,{r['phase']},{r['rep']},{r['p50_ms']},{r['p90_ms']},{r['p99_ms']},{r['p999_ms']},{r['actual_qps']}\n")


def run_workload_05(reps):
    """Workload 05: Multi-Tenant Mix."""
    log("\n=== Executing Workload 05 (Multi-Tenant Mix) for qos_markov ===")
    wl_dir = os.path.join(RESULTS_DIR, "workload_05")
    raw_dir = os.path.join(wl_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    results = []
    for rep in range(1, reps + 1):
        trial_id = f"qos_markov_multitenant_rep_{rep}"
        log(f"[Workload 05] Trial {trial_id}")

        run_cmd(f"{os.path.join(CLB_DIR, 'setup_topology.sh')}", cwd=CLB_DIR)
        run_cmd(f"{os.path.join(CLB_DIR, 'attach_ebpf.sh')} qos_dynamic", cwd=CLB_DIR)

        ctrl_log = os.path.join(raw_dir, f"qos_controller_{trial_id}.jsonl")
        ctrl_proc = subprocess.Popen(["python3", CONTROLLER_SCRIPT, "--log-file", ctrl_log, "--interval", "0.1"])
        time.sleep(1)

        for v_id in [1, 2]:
            run_cmd(f"runc kill victim_container_{v_id} KILL 2>/dev/null || true", check=False)
            run_cmd(f"runc delete victim_container_{v_id} 2>/dev/null || true", check=False)
            prepare_victim_bundle(CLB_DIR, v_id, "http", 8080)
            run_cmd(f"runc run --bundle {os.path.join(CLB_DIR, f'victim_bundle_{v_id}')} -d victim_container_{v_id} >/dev/null 2>&1", check=False)

        wait_for_port("10.0.0.10", 8080)
        wait_for_port("10.0.0.11", 8080)

        att_proc = start_hping("u20")
        time.sleep(2.0)

        out_v1 = os.path.join(raw_dir, f"fortio_{trial_id}_vic1.json")
        out_v2 = os.path.join(raw_dir, f"fortio_{trial_id}_vic2.json")

        cmd1 = f"taskset -c 0 fortio load -c 2 -qps 50 -t 15s -json {out_v1} http://10.0.0.10:8080/"
        cmd2 = f"taskset -c 1 fortio load -c 4 -qps 200 -t 15s -json {out_v2} http://10.0.0.11:8080/"

        p1 = subprocess.Popen(cmd1, shell=True)
        p2 = subprocess.Popen(cmd2, shell=True)
        p1.wait()
        p2.wait()

        if att_proc:
            att_proc.kill()
            run_cmd("pkill -9 hping3 2>/dev/null || true", check=False)
        ctrl_proc.kill()
        for v_id in [1, 2]:
            run_cmd(f"runc kill victim_container_{v_id} KILL 2>/dev/null || true", check=False)
            run_cmd(f"runc delete victim_container_{v_id} 2>/dev/null || true", check=False)

        m1 = parse_fortio_json(out_v1)
        m2 = parse_fortio_json(out_v2)
        results.append({"arch": "qos_markov", "victim_id": 1, "rep": rep, **m1})
        results.append({"arch": "qos_markov", "victim_id": 2, "rep": rep, **m2})

    raw_csv = os.path.join(wl_dir, "results_summary_raw.csv")
    with open(raw_csv, "w") as f:
        f.write("arch,protocol,victim_id,rep,p50_ms,p90_ms,p99_ms,p999_ms,actual_qps\n")
        for r in results:
            f.write(f"{r['arch']},http,{r['victim_id']},{r['rep']},{r['p50_ms']},{r['p90_ms']},{r['p99_ms']},{r['p999_ms']},{r['actual_qps']}\n")


def run_workload_06(reps):
    """Workload 06: Retry Storm Amplification."""
    log("\n=== Executing Workload 06 (Retry Storm) for qos_markov ===")
    wl_dir = os.path.join(RESULTS_DIR, "workload_06")
    raw_dir = os.path.join(wl_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    results = []
    for rep in range(1, reps + 1):
        trial_id = f"qos_markov_retry_rep_{rep}"
        log(f"[Workload 06] Trial {trial_id}")

        run_cmd(f"{os.path.join(CLB_DIR, 'setup_topology.sh')}", cwd=CLB_DIR)
        run_cmd(f"{os.path.join(CLB_DIR, 'attach_ebpf.sh')} qos_dynamic", cwd=CLB_DIR)

        ctrl_log = os.path.join(raw_dir, f"qos_controller_{trial_id}.jsonl")
        ctrl_proc = subprocess.Popen(["python3", CONTROLLER_SCRIPT, "--log-file", ctrl_log, "--interval", "0.1"])
        time.sleep(1)

        run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
        run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)
        prepare_victim_bundle(CLB_DIR, 1, "http", 8080)
        run_cmd(f"runc run --bundle {os.path.join(CLB_DIR, 'victim_bundle_1')} -d victim_container_1 >/dev/null 2>&1", check=False)

        if not wait_for_port("10.0.0.10", 8080):
            ctrl_proc.kill()
            continue

        out_json = os.path.join(raw_dir, f"fortio_{trial_id}.json")
        f_cmd = f"taskset -c 0 fortio load -c 2 -qps 50 -t 15s -json {out_json} http://10.0.0.10:8080/"
        subprocess.run(f_cmd, shell=True, capture_output=True, text=True)

        ctrl_proc.kill()
        run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
        run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)

        metrics = parse_fortio_json(out_json)
        results.append({"arch": "qos_markov", "pattern": "retry_storm", "rep": rep, **metrics})

    raw_csv = os.path.join(wl_dir, "results_summary_raw.csv")
    with open(raw_csv, "w") as f:
        f.write("arch,protocol,pattern,rep,p50_ms,p90_ms,p99_ms,p999_ms,actual_qps\n")
        for r in results:
            f.write(f"{r['arch']},http,{r['pattern']},{r['rep']},{r['p50_ms']},{r['p90_ms']},{r['p99_ms']},{r['p999_ms']},{r['actual_qps']}\n")


def main():
    parser = argparse.ArgumentParser(description="Standalone Runner for qos_markov across Workloads 01..06")
    parser.add_argument("--reps", type=int, default=10, help="Number of repetitions")
    args = parser.parse_args()

    if os.geteuid() != 0:
        log("ERROR: Must be run as root (sudo python3 run_markov_runner.py)")
        sys.exit(1)

    log(f"Starting qos_markov Workload Runner (reps={args.reps})...")
    os.makedirs(RESULTS_DIR, exist_ok=True)

    run_workload_01(args.reps)
    run_workload_02(args.reps)
    run_workload_03(args.reps)
    run_workload_04(args.reps)
    run_workload_05(args.reps)
    run_workload_06(args.reps)

    log("\nAll 6 Workloads successfully completed for qos_markov! Results saved in markov_experiments/results/")


if __name__ == "__main__":
    main()
