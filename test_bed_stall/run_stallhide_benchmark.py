#!/usr/bin/env python3
"""
run_stallhide_benchmark.py — Micro-Benchmark Runner for Naive vs Stallhide Limiters.

Matrix:
    - Limiter Variants: naive, stallhide
    - Flood Levels: 0, u500, u200, u50, u20, u5, u2, flood (configurable)
    - Repetitions: N=2 (pilot) or N=10 (full matrix)
    - Protocol: HTTP only

Usage:
    sudo python3 run_stallhide_benchmark.py [--reps 2] [--flood-levels 0 u20 flood]
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

SHARED_CONFIG = {
    "qps": 50,
    "conns": 2,
    "duration_sec": 10,
    "warmup_sec": 2,
    "protocol": "http",
    "port": 8080,
    "victim_ip": "10.0.0.10",
    "attacker_ip": "10.0.0.20"
}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def run_cmd(cmd, check=True, shell=True):
    res = subprocess.run(cmd, shell=shell, capture_output=True, text=True)
    if check and res.returncode != 0:
        log(f"CRITICAL ERROR: Command failed: {cmd}\nStderr: {res.stderr}")
    return res

def wait_for_port(ip, port, timeout=15):
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection((ip, port), timeout=1.0):
                return True
        except (OSError, ConnectionRefusedError):
            time.sleep(0.5)
    log(f"CRITICAL ERROR: Timeout waiting for server {ip}:{port}!")
    return False

def prepare_victim_bundle(victim_id, protocol, port):
    bundle_name = f"victim_bundle_{victim_id}"
    run_cmd(f"rm -rf {bundle_name} && cp -r victim_bundle {bundle_name}")

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

def start_attacker_flood(flood_level):
    if flood_level == "0":
        return None, "ip netns exec ns_attacker true"

    if flood_level == "flood":
        cmd = f"ip netns exec ns_attacker hping3 --udp -p {SHARED_CONFIG['port']} --flood {SHARED_CONFIG['victim_ip']}"
    else:
        cmd = f"ip netns exec ns_attacker hping3 --udp -p {SHARED_CONFIG['port']} -i {flood_level} {SHARED_CONFIG['victim_ip']}"

    proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    return proc, cmd

def run_single_trial(variant, flood_level, rep, raw_dir):
    trial_id = f"{variant}_flood_{flood_level}_rep_{rep}"
    log(f"--- Running Trial: {trial_id} ---")

    # 1. Setup Topology & Attach eBPF
    run_cmd("./setup_topology.sh")
    run_cmd(f"./attach_ebpf.sh {variant}")

    # 2. Start Stackelberg QoS Controller
    controller_log = os.path.join(raw_dir, f"qos_controller_{trial_id}.jsonl")
    controller_proc = subprocess.Popen(["python3", "qos_controller.py", "--log-file", controller_log, "--interval", "0.2"])
    time.sleep(1)

    # 3. Start Victim Container
    run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
    run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)
    time.sleep(1)
    prepare_victim_bundle(1, "http", SHARED_CONFIG["port"])

    r_res = run_cmd("runc run --bundle victim_bundle_1 -d victim_container_1 >/dev/null 2>&1", check=False)
    if r_res.returncode != 0:
        log(f"CRITICAL ERROR: Container run failed: {r_res.stderr}")
        controller_proc.kill()
        sys.exit(1)

    if not wait_for_port(SHARED_CONFIG["victim_ip"], SHARED_CONFIG["port"]):
        controller_proc.kill()
        sys.exit(1)

    # 4. Start Attack Flood
    attacker_proc, attacker_cmd = start_attacker_flood(flood_level)

    # 5. Start Instrumentation Pollers
    ebpf_log = os.path.join(raw_dir, f"ebpf_stats_{trial_id}.jsonl")
    runq_log = os.path.join(raw_dir, f"runq_stats_{trial_id}.jsonl")
    bpftrace_log = os.path.join(raw_dir, f"bpftrace_lock_{trial_id}.jsonl")
    net_log = os.path.join(raw_dir, f"network_{trial_id}.jsonl")

    ebpf_poller = subprocess.Popen(["python3", "collect_ebpf_stats.py", ebpf_log])
    runq_poller = subprocess.Popen(["python3", "collect_runq_stats.py", runq_log])
    bpftrace_poller = subprocess.Popen(["sudo", "bpftrace", "collect_bpftrace_lock.bt"], stdout=open(bpftrace_log, "w"), stderr=subprocess.DEVNULL)
    net_poller = subprocess.Popen(["./collect_network_stats.sh", net_log])

    time.sleep(SHARED_CONFIG["warmup_sec"])

    # 6. Execute Fortio Load
    fortio_json = os.path.join(raw_dir, f"fortio_{trial_id}.json")
    f_cmd = f"taskset -c 0 fortio load -c {SHARED_CONFIG['conns']} -qps {SHARED_CONFIG['qps']} -t {SHARED_CONFIG['duration_sec']}s -json {fortio_json} http://{SHARED_CONFIG['victim_ip']}:{SHARED_CONFIG['port']}/"
    res = subprocess.run(f_cmd, shell=True, capture_output=True, text=True)

    # Cleanup trial background processes
    ebpf_poller.send_signal(signal.SIGINT)
    runq_poller.send_signal(signal.SIGINT)
    bpftrace_poller.send_signal(signal.SIGINT)
    net_poller.send_signal(signal.SIGINT)
    controller_proc.kill()

    if attacker_proc:
        attacker_proc.kill()
        run_cmd("pkill -9 hping3 2>/dev/null || true", check=False)

    run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
    run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)

    if res.returncode != 0:
        log(f"CRITICAL ERROR: Fortio run failed for trial {trial_id}!\nStderr: {res.stderr}")
        sys.exit(1)

    time.sleep(1.0)
    return fortio_json

def parse_fortio_metrics(json_file):
    try:
        with open(json_file, "r") as f:
            data = json.load(f)

        dur_hist = data.get("DurationHistogram", {})
        actual_qps = dur_hist.get("Avg", 0.0)

        percentiles = {}
        for p in dur_hist.get("Percentiles", []):
            percentiles[p["Percentile"]] = p["Value"] * 1000.0  # Convert seconds to ms

        return {
            "p50_ms": round(percentiles.get(50.0, 0.0), 3),
            "p90_ms": round(percentiles.get(90.0, 0.0), 3),
            "p99_ms": round(percentiles.get(99.0, 0.0), 3),
            "p999_ms": round(percentiles.get(99.9, 0.0), 3),
            "actual_qps": round(actual_qps, 2)
        }
    except Exception as e:
        log(f"Error parsing fortio metrics from {json_file}: {e}")
        return {"p50_ms": 0.0, "p90_ms": 0.0, "p99_ms": 0.0, "p999_ms": 0.0, "actual_qps": 0.0}

def main():
    parser = argparse.ArgumentParser(description="Micro-Benchmark Runner for Naive vs Stallhide eBPF Limiters")
    parser.add_argument("--reps", type=int, default=2, help="Number of repetitions per cell (default: 2)")
    parser.add_argument("--variants", nargs="+", default=["naive", "stallhide"], help="Limiter variants")
    parser.add_argument("--flood-levels", nargs="+", default=["0", "u20", "flood"], help="Flood levels to run")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for trial order")
    args = parser.parse_args()

    if os.geteuid() != 0:
        log("CRITICAL ERROR: Must be run as root (sudo python3 run_stallhide_benchmark.py)")
        sys.exit(1)

    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join("results", f"multi_run_N{args.reps}_{ts_str}")
    raw_dir = os.path.join(run_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    trials = []
    for var in args.variants:
        for fl in args.flood_levels:
            for rep in range(1, args.reps + 1):
                trials.append({"variant": var, "flood_level": fl, "rep": rep})

    random.seed(args.seed)
    random.shuffle(trials)

    manifest = {
        "timestamp": ts_str,
        "reps": args.reps,
        "variants": args.variants,
        "flood_levels": args.flood_levels,
        "total_trials": len(trials),
        "seed": args.seed,
        "trials": trials
    }
    with open(os.path.join(run_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    log(f"=======================================================")
    log(f"=== Starting Micro-Benchmark Run: N={args.reps} Reps ===")
    log(f"=== Total Trials: {len(trials)} | Output: {run_dir} ===")
    log(f"=======================================================")

    results_raw = []
    for idx, t in enumerate(trials, 1):
        log(f"\n[Trial {idx}/{len(trials)}] Variant: {t['variant']} | Flood: {t['flood_level']} | Rep: {t['rep']}")
        fortio_json = run_single_trial(t["variant"], t["flood_level"], t["rep"], raw_dir)
        metrics = parse_fortio_metrics(fortio_json)

        row = {
            "variant": t["variant"],
            "protocol": "http",
            "flood_level": t["flood_level"],
            "rep": t["rep"],
            **metrics
        }
        results_raw.append(row)

    # Write CSV summaries
    raw_csv = os.path.join(run_dir, "results_summary_raw.csv")
    with open(raw_csv, "w") as f:
        f.write("variant,protocol,flood_level,rep,p50_ms,p90_ms,p99_ms,p999_ms,actual_qps\n")
        for r in results_raw:
            f.write(f"{r['variant']},{r['protocol']},{r['flood_level']},{r['rep']},{r['p50_ms']},{r['p90_ms']},{r['p99_ms']},{r['p999_ms']},{r['actual_qps']}\n")

    # Compute per-cell averages
    cell_groups = {}
    for r in results_raw:
        cell_key = (r["variant"], r["protocol"], r["flood_level"])
        if cell_key not in cell_groups:
            cell_groups[cell_key] = []
        cell_groups[cell_key].append(r)

    avg_csv = os.path.join(run_dir, "results_summary_avg.csv")
    with open(avg_csv, "w") as f:
        f.write("variant,protocol,flood_level,reps_count,p50_ms,p90_ms,p99_ms,p999_ms,actual_qps\n")
        for (var, proto, fl), rows in cell_groups.items():
            count = len(rows)
            p50_avg = round(sum(r["p50_ms"] for r in rows) / count, 3)
            p90_avg = round(sum(r["p90_ms"] for r in rows) / count, 3)
            p99_avg = round(sum(r["p99_ms"] for r in rows) / count, 3)
            p999_avg = round(sum(r["p999_ms"] for r in rows) / count, 3)
            qps_avg = round(sum(r["actual_qps"] for r in rows) / count, 2)
            f.write(f"{var},{proto},{fl},{count},{p50_avg},{p90_avg},{p99_avg},{p999_avg},{qps_avg}\n")

    log(f"\nMicro-Benchmark Run Complete! Summaries written to {run_dir}")

if __name__ == "__main__":
    main()
