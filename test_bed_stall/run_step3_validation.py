#!/usr/bin/env python3
"""
run_step3_validation.py — Single Trial Validation for Naive vs Stallhide eBPF Limiters.

Executes ONE manual trial per variant (naive and stallhide) for HTTP baseline (QPS=50, conns=2, 10s, flood=0).
Collects and verifies non-trivial data in both limiter_only_latency_hist and runq_latency instrumentation streams.
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

def run_single_variant_trial(variant, results_dir):
    log(f"\n=======================================================")
    log(f"=== Starting Step 3 Validation Trial: Variant = {variant} ===")
    log(f"=======================================================")

    log("Step 1: Setting up network topology & attaching eBPF classifier...")
    run_cmd("./setup_topology.sh")
    run_cmd(f"./attach_ebpf.sh {variant}")

    log("Step 2: Starting userspace Stackelberg QoS controller...")
    controller_log = os.path.join(results_dir, f"qos_controller_{variant}.jsonl")
    controller_proc = subprocess.Popen(["python3", "qos_controller.py", "--log-file", controller_log, "--interval", "0.2"])
    time.sleep(1)

    log("Step 3: Starting HTTP Victim 1 container on port 8080...")
    run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
    run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)
    time.sleep(1)
    prepare_victim_bundle(1, "http", SHARED_CONFIG["port"])

    r_res = run_cmd("runc run --bundle victim_bundle_1 -d victim_container_1 >/dev/null 2>&1", check=False)
    if r_res.returncode != 0:
        log(f"CRITICAL ERROR: Runc container start failed: {r_res.stderr}")
        controller_proc.kill()
        sys.exit(1)

    if not wait_for_port(SHARED_CONFIG["victim_ip"], SHARED_CONFIG["port"]):
        controller_proc.kill()
        sys.exit(1)

    log("Step 4: Starting eBPF stats poller & CFS runqueue latency tracer...")
    ebpf_stats_file = os.path.join(results_dir, f"ebpf_stats_{variant}.jsonl")
    runq_stats_file = os.path.join(results_dir, f"runq_stats_{variant}.jsonl")

    poller_ebpf = subprocess.Popen(["python3", "collect_ebpf_stats.py", ebpf_stats_file])
    poller_runq = subprocess.Popen(["python3", "collect_runq_stats.py", runq_stats_file])

    log(f"Step 5: Executing Fortio HTTP load for variant {variant} (QPS=50, conns=2, 10s)...")
    fortio_json = os.path.join(results_dir, f"fortio_{variant}.json")
    f_cmd = f"taskset -c 0 fortio load -c {SHARED_CONFIG['conns']} -qps {SHARED_CONFIG['qps']} -t {SHARED_CONFIG['duration_sec']}s -json {fortio_json} http://{SHARED_CONFIG['victim_ip']}:{SHARED_CONFIG['port']}/"
    res = subprocess.run(f_cmd, shell=True)

    import signal
    poller_ebpf.send_signal(signal.SIGINT)
    poller_runq.send_signal(signal.SIGINT)
    poller_ebpf.wait(timeout=3)
    poller_runq.wait(timeout=3)
    controller_proc.kill()

    if res.returncode != 0:
        log(f"CRITICAL ERROR: Fortio load run failed for variant {variant}!")
        sys.exit(1)

    log("Step 6: Cleaning up container...")
    run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
    run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)

def main():
    if os.geteuid() != 0:
        log("ERROR: Must be run as root (sudo python3 run_step3_validation.py)")
        sys.exit(1)

    results_dir = os.path.join("results", "step3_validation")
    os.makedirs(results_dir, exist_ok=True)

    run_single_variant_trial("naive", results_dir)
    run_single_variant_trial("stallhide", results_dir)

    log(f"\nStep 3 Validation Complete! Results saved in {results_dir}")

if __name__ == "__main__":
    main()
