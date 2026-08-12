#!/usr/bin/env python3
"""
run_step2_tiered_trial.py — Single Trial Validation for Static Priority-Tier QoS (ebpf_qos_tiered.c).
Runs ONE HTTP baseline trial (QPS=50, conns=2, 10s, flood=0) with ebpf_qos_tiered.c attached.
Dumps raw Fortio JSON and eBPF map stats.
"""

import os
import sys
import json
import time
import socket
import subprocess
from datetime import datetime

SHARED_CONFIG = {
    "qps": 50,
    "conns": 2,
    "duration_sec": 10,
    "warmup_sec": 2,
    "victim_ip": "10.0.0.10",
    "port": 8080
}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def run_cmd(cmd, check=True, shell=True):
    res = subprocess.run(cmd, shell=shell, capture_output=True, text=True)
    if check and res.returncode != 0:
        log(f"Command failed: {cmd}\nStderr: {res.stderr}")
    return res

def wait_for_port(ip, port, timeout=15):
    log(f"Polling TCP socket {ip}:{port} until ready...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection((ip, port), timeout=1.0):
                log(f"Server {ip}:{port} is READY ({round(time.time()-start, 2)}s).")
                return True
        except (OSError, ConnectionRefusedError):
            time.sleep(0.5)
    log(f"CRITICAL ERROR: Timeout waiting for server {ip}:{port} to respond!")
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

def main():
    if os.geteuid() != 0:
        log("ERROR: Must be run as root.")
        sys.exit(1)

    results_dir = os.path.join("results", "step2_tiered_single_trial")
    os.makedirs(results_dir, exist_ok=True)

    log("Step 1: Setting up network topology & attaching ebpf_qos_tiered.c...")
    run_cmd("./setup_topology.sh")
    run_cmd("./attach_ebpf.sh qos_tiered")

    log("Step 2: Starting HTTP Victim 1 container on port 8080...")
    run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
    run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)
    time.sleep(1)
    prepare_victim_bundle(1, "http", SHARED_CONFIG["port"])

    r_res = run_cmd("runc run --bundle victim_bundle_1 -d victim_container_1 >/dev/null 2>&1", check=False)
    if r_res.returncode != 0:
        log(f"CRITICAL ERROR: Runc container start failed: {r_res.stderr}")
        sys.exit(1)

    if not wait_for_port(SHARED_CONFIG["victim_ip"], SHARED_CONFIG["port"]):
        sys.exit(1)

    log("Step 3: Starting eBPF stats poller...")
    ebpf_stats_file = os.path.join(results_dir, "ebpf_stats_tiered_baseline.jsonl")
    poller = subprocess.Popen(["python3", "collect_ebpf_stats.py", ebpf_stats_file])

    log("Step 4: Executing single Fortio HTTP trial (QPS=50, conns=2, 10s, flood=0)...")
    fortio_json = os.path.join(results_dir, "fortio_tiered_http_baseline.json")
    f_cmd = f"taskset -c 0 fortio load -c {SHARED_CONFIG['conns']} -qps {SHARED_CONFIG['qps']} -t {SHARED_CONFIG['duration_sec']}s -json {fortio_json} http://{SHARED_CONFIG['victim_ip']}:{SHARED_CONFIG['port']}/"
    res = subprocess.run(f_cmd, shell=True)

    poller.kill()

    if res.returncode != 0:
        log("CRITICAL ERROR: Fortio load run failed!")
        sys.exit(1)

    log("Step 5: Cleaning up container...")
    run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
    run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)

    log(f"Step 2 Single Trial Complete! Results saved in {results_dir}")

if __name__ == "__main__":
    main()
