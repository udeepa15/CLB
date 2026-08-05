#!/usr/bin/env python3
"""
run_stage3_pilot.py — Stage 3 Pilot (2 Repetitions) with Full Instrumentation & Manifest.
Supports: HTTP & UDP protocols across sidecarless & sidecar architectures.
Executes Flood-Intensity Sweep (0, u200, u20, u2, u1, flood) and Noisy-Node Sweep (0-5).
Generates randomized manifest.json prior to run.
"""

import os
import sys
import json
import time
import random
import socket
import subprocess
from datetime import datetime

SEED = 42
REPETITIONS = 2  # Pilot run: 2 reps per cell

CONFIG = {
    "qps": 50,
    "conns": 2,
    "duration_sec": 10,
    "protocols": ["http", "udp"],
    "archs": ["sidecarless", "sidecar"],
    "ports": {"http": 8080, "udp": 8078},
    "flood_levels": ["0", "u200", "u20", "u2", "u1", "flood"],
    "noisy_nodes": [0, 1, 2, 3, 4, 5]
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
    log(f"CRITICAL ERROR: Timeout waiting for TCP server {ip}:{port}!")
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

def generate_manifest(results_dir):
    random.seed(SEED)
    manifest = []
    
    # 1. Single-Victim Flood Intensity Sweep
    for arch in CONFIG["archs"]:
        for proto in CONFIG["protocols"]:
            for level in CONFIG["flood_levels"]:
                for rep in range(REPETITIONS):
                    manifest.append({
                        "sweep_type": "flood_intensity",
                        "arch": arch,
                        "protocol": proto,
                        "level": level,
                        "rep": rep
                    })
                    
    # 2. 10-Node Cluster Noisy Node Sweep
    for arch in CONFIG["archs"]:
        for proto in CONFIG["protocols"]:
            for noisy_count in CONFIG["noisy_nodes"]:
                for rep in range(REPETITIONS):
                    manifest.append({
                        "sweep_type": "noisy_nodes",
                        "arch": arch,
                        "protocol": proto,
                        "noisy_count": noisy_count,
                        "rep": rep
                    })
                    
    random.shuffle(manifest)
    
    manifest_file = os.path.join(results_dir, "manifest.json")
    with open(manifest_file, "w") as f:
        json.dump({"seed": SEED, "reps": REPETITIONS, "total_trials": len(manifest), "trials": manifest}, f, indent=2)
        
    log(f"Manifest written to {manifest_file} with {len(manifest)} randomized trial runs.")
    return manifest

def run_single_victim_trial(trial, results_dir):
    arch = trial["arch"]
    proto = trial["protocol"]
    level = trial["level"]
    rep = trial["rep"]
    port = CONFIG["ports"][proto]
    
    log(f"[Trial] Single-Victim | Arch: {arch} | Proto: {proto} | Flood: {level} | Rep: {rep}")
    
    # Infra Setup
    run_cmd("./setup_topology.sh")
    if arch == "sidecarless":
        run_cmd("./attach_ebpf.sh")
    else:
        run_cmd("pkill -9 -f 'socat' 2>/dev/null || true", check=False)
        for dev in ["veth-att-br", "br-mesh", "veth-vic1-br"]:
            run_cmd(f"tc qdisc del dev {dev} clsact 2>/dev/null || true", check=False)
            
    # Spawn Attacker Container
    run_cmd("runc kill attacker_container KILL 2>/dev/null || true", check=False)
    run_cmd("runc delete attacker_container 2>/dev/null || true", check=False)
    run_cmd("runc run --bundle attacker_bundle -d attacker_container >/dev/null 2>&1")
    
    # Spawn Victim 1 Container
    run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
    run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)
    time.sleep(1)
    prepare_victim_bundle(1, proto, port)
    r_res = run_cmd("runc run --bundle victim_bundle_1 -d victim_container_1 >/dev/null 2>&1", check=False)
    if r_res.returncode != 0:
        log(f"CRITICAL ERROR: Runc start failed: {r_res.stderr}")
        sys.exit(1)
        
    # Poll Socket Readiness
    ready = wait_for_port("10.0.0.10", port) if proto == "http" else wait_for_udp("10.0.0.10", port)
    if not ready:
        sys.exit(1)

    # Setup Sidecar Reverse Proxy if needed
    if arch == "sidecar":
        socat_cmd = f"nsenter --net=/var/run/netns/ns_victim1 socat TCP-LISTEN:8080,fork,reuseaddr TCP:127.0.0.1:{port} >/dev/null 2>&1 &"
        run_cmd(socat_cmd, check=False)
        run_cmd(f"ip netns exec ns_victim1 iptables -t nat -A PREROUTING -p tcp --dport {port} -j REDIRECT --to-ports 8080", check=False)
        time.sleep(1)
        
    # Start Attacker Flood Traffic
    if level != "0":
        p_arg = "8078" if proto in ["udp", "tcp"] else str(port)
        if level == "flood":
            h_cmd = f"nsenter --net=/var/run/netns/ns_attacker hping3 --flood --udp -p {p_arg} 10.0.0.10 >/dev/null 2>&1 &"
        else:
            micro_sec = level.replace("u", "")
            h_cmd = f"nsenter --net=/var/run/netns/ns_attacker hping3 --udp -i u{micro_sec} -p {p_arg} 10.0.0.10 >/dev/null 2>&1 &"
        run_cmd(h_cmd, check=False)
        time.sleep(1)

    # Start Instrumentation Collectors
    prefix = f"{arch}_{proto}_flood_{level}_rep_{rep}"
    ebpf_out = os.path.join(results_dir, f"ebpf_stats_{prefix}.jsonl")
    cgroup_out = os.path.join(results_dir, f"cgroup_stats_{prefix}.csv")
    fortio_out = os.path.join(results_dir, f"fortio_{prefix}.json")

    p_ebpf = subprocess.Popen(["python3", "collect_ebpf_stats.py", ebpf_out])
    p_cgroup = subprocess.Popen(["python3", "collect_cgroup_stats.py", "/sys/fs/cgroup/victim_container_1", cgroup_out])

    # Run Fortio Workload
    target_url = f"http://10.0.0.10:{port}/" if proto == "http" else f"udp://10.0.0.10:{port}"
    f_cmd = f"taskset -c 0 fortio load -c {CONFIG['conns']} -qps {CONFIG['qps']} -t {CONFIG['duration_sec']}s -json {fortio_out} {target_url}"
    res = subprocess.run(f_cmd, shell=True)

    # Stop Collectors & Attackers
    p_ebpf.kill()
    p_cgroup.kill()
    run_cmd("pkill -9 -f 'hping3' 2>/dev/null || true", check=False)
    run_cmd("./collect_network_stats.sh veth-vic1-br " + results_dir + " " + prefix, check=False)

    if res.returncode != 0:
        log(f"CRITICAL ERROR: Fortio failed for trial {prefix}!")
        sys.exit(1)

def main():
    if os.geteuid() != 0:
        log("ERROR: Must be run as root.")
        sys.exit(1)

    results_dir = os.path.join("results", "stage3_pilot")
    os.makedirs(results_dir, exist_ok=True)

    manifest = generate_manifest(results_dir)
    log("=== Starting Stage 3 Pilot Execution (2 Repetitions) ===")

    for idx, trial in enumerate(manifest):
        log(f"\n--- Progress: {idx+1}/{len(manifest)} trials ---")
        if trial["sweep_type"] == "flood_intensity":
            run_single_victim_trial(trial, results_dir)
        else:
            # Skip multi-node cluster pilot in single-victim runner for now
            pass

    log(f"\nStage 3 Pilot complete! Output files saved in {results_dir}")

if __name__ == "__main__":
    main()
