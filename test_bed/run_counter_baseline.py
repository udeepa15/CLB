#!/usr/bin/env python3
"""
run_counter_baseline.py — Fail-Fast Baseline Run (HTTP & UDP) with eBPF Counter Poller.
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
    "victim_ip": "10.0.0.10"
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

def wait_for_udp(ip, port, timeout=15):
    log(f"Polling UDP socket {ip}:{port} until ready...")
    start = time.time()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.0)
    msg = b"PING"
    while time.time() - start < timeout:
        try:
            sock.sendto(msg, (ip, port))
            data, _ = sock.recvfrom(64)
            if data:
                log(f"UDP Server {ip}:{port} is READY ({round(time.time()-start, 2)}s).")
                sock.close()
                return True
        except Exception:
            time.sleep(0.5)
    sock.close()
    log(f"CRITICAL ERROR: Timeout waiting for UDP server {ip}:{port}!")
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
        
    results_dir = os.path.join("results", "manual_counter")
    os.makedirs(results_dir, exist_ok=True)

    log("Step 1: Setting up network topology & eBPF classifiers...")
    run_cmd("./setup_topology.sh")
    run_cmd("./attach_ebpf.sh")

    # ── HTTP Baseline ────────────────────────────────────────────────────────
    log("Step 2: Starting HTTP Victim 1 container on port 80...")
    run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
    run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)
    time.sleep(1)
    prepare_victim_bundle(1, "http", 80)
    
    r_res = run_cmd("runc run --bundle victim_bundle_1 -d victim_container_1", check=False)
    if r_res.returncode != 0:
        log(f"CRITICAL: Runc HTTP container failed to start: {r_res.stderr}")
        sys.exit(1)

    if not wait_for_port("10.0.0.10", 80, timeout=15):
        run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
        sys.exit(1)

    log("Starting eBPF counter poller for HTTP baseline...")
    http_poller_file = os.path.join(results_dir, "http_ebpf_stats.jsonl")
    poller = subprocess.Popen(["python3", "collect_ebpf_stats.py", http_poller_file])

    log("Running Fortio HTTP baseline load (QPS=50, conns=2, 10s)...")
    http_json = os.path.join(results_dir, "http_baseline.json")
    f_cmd = f"taskset -c 0 fortio load -c {SHARED_CONFIG['conns']} -qps {SHARED_CONFIG['qps']} -t {SHARED_CONFIG['duration_sec']}s -json {http_json} http://10.0.0.10:80/"
    res = subprocess.run(f_cmd, shell=True)

    poller.kill()
    if res.returncode != 0:
        log("CRITICAL ERROR: Fortio HTTP baseline run FAILED with non-zero exit code!")
        sys.exit(1)
    time.sleep(1)

    # ── UDP Baseline ─────────────────────────────────────────────────────────
    log("Step 3: Starting UDP Victim 1 container on port 8078...")
    run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
    run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)
    time.sleep(1)
    prepare_victim_bundle(1, "udp", 8078)
    
    r_res = run_cmd("runc run --bundle victim_bundle_1 -d victim_container_1", check=False)
    if r_res.returncode != 0:
        log(f"CRITICAL: Runc UDP container failed to start: {r_res.stderr}")
        sys.exit(1)

    if not wait_for_udp("10.0.0.10", 8078, timeout=15):
        run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
        sys.exit(1)

    log("Starting eBPF counter poller for UDP baseline...")
    udp_poller_file = os.path.join(results_dir, "udp_ebpf_stats.jsonl")
    poller = subprocess.Popen(["python3", "collect_ebpf_stats.py", udp_poller_file])

    log("Running Fortio UDP baseline load (QPS=50, conns=2, 10s)...")
    udp_json = os.path.join(results_dir, "udp_baseline.json")
    f_cmd = f"taskset -c 0 fortio load -c {SHARED_CONFIG['conns']} -qps {SHARED_CONFIG['qps']} -t {SHARED_CONFIG['duration_sec']}s -json {udp_json} udp://10.0.0.10:8078"
    res = subprocess.run(f_cmd, shell=True)

    poller.kill()
    if res.returncode != 0:
        log("CRITICAL ERROR: Fortio UDP baseline run FAILED with non-zero exit code!")
        sys.exit(1)

    # ── Teardown ─────────────────────────────────────────────────────────────
    log("Step 4: Cleaning up containers...")
    run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
    run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)

    log(f"SUCCESS: Manual counter baseline run finished cleanly. Results saved in {results_dir}")

if __name__ == "__main__":
    main()
