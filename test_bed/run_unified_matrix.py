#!/usr/bin/env python3
"""
run_unified_matrix.py — Unified Multi-Protocol & Architectural Benchmark Matrix

Supports:
  - Architecture: sidecarless | sidecar
  - Protocols: http | grpc | tcp | udp
  - Flood Levels: 0, u200, u20, u2, u1, flood

Usage:
  sudo python3 run_unified_matrix.py --arch sidecarless --protocol all
  sudo python3 run_unified_matrix.py --arch sidecar --protocol grpc
  python3 run_unified_matrix.py --dry-run
"""

import argparse
import csv
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime

# Default Baseline Parameters
SHARED_CONFIG = {
    "qps": 50,
    "conns": 2,
    "duration_sec": 10,
    "warmup_sec": 2,
    "flood_arr": ["0", "u200", "u20", "u2", "u1", "flood"],
    "supported_protocols": ["http", "grpc", "tcp", "udp"],
    "supported_archs": ["sidecarless", "sidecar"],
    "ports": {
        "http": 80,
        "grpc": 8079,
        "tcp": 8078,
        "udp": 8078,
    },
    "victims": [
        {"id": 1, "ip": "10.0.0.10", "cpu": 1},
        {"id": 2, "ip": "10.0.0.11", "cpu": 2},
        {"id": 3, "ip": "10.0.0.12", "cpu": 3},
    ]
}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def run_cmd(cmd, check=True, shell=True):
    res = subprocess.run(cmd, shell=shell, capture_output=True, text=True)
    if check and res.returncode != 0:
        log(f"Command failed: {cmd}\nStderr: {res.stderr}")
    return res

def cleanup():
    log("Cleaning up active processes and netns state...")
    run_cmd("pkill -9 -f 'hping3' 2>/dev/null || true", check=False)
    run_cmd("pkill -9 -f 'socat' 2>/dev/null || true", check=False)
    run_cmd("pkill -9 -f 'fortio server' 2>/dev/null || true", check=False)
    
    for i in [1, 2, 3]:
        run_cmd(f"runc kill victim_container_{i} KILL 2>/dev/null || true", check=False)
        run_cmd(f"runc delete victim_container_{i} 2>/dev/null || true", check=False)
        run_cmd(f"ip netns exec ns_victim{i} iptables -t nat -F 2>/dev/null || true", check=False)
        
    run_cmd("runc kill attacker_container KILL 2>/dev/null || true", check=False)
    run_cmd("runc delete attacker_container 2>/dev/null || true", check=False)
    
    for dev in ["veth-att-br", "br-mesh", "veth-vic1-br", "veth-vic2-br", "veth-vic3-br"]:
        run_cmd(f"tc qdisc del dev {dev} clsact 2>/dev/null || true", check=False)
        
    run_cmd("rm -f /sys/fs/bpf/tc/globals/flow_map /sys/fs/bpf/ip/globals/flow_map 2>/dev/null || true", check=False)

def prepare_victim_bundle(victim_id, protocol, port):
    bundle_name = f"victim_bundle_{victim_id}"
    run_cmd(f"rm -rf {bundle_name} && cp -r victim_bundle {bundle_name}")
    
    config_path = os.path.join(bundle_name, "config.json")
    with open(config_path, "r") as f:
        config = json.load(f)
        
    # Update netns path & cpuset
    for ns in config["linux"]["namespaces"]:
        if ns["type"] == "network":
            ns["path"] = f"/var/run/netns/ns_victim{victim_id}"
            
    if "resources" not in config["linux"]:
        config["linux"]["resources"] = {}
    config["linux"]["resources"]["cpu"] = {"cpus": str(victim_id)}
    
    # Set server command
    config["process"]["args"] = [
        "sh", "-c",
        f"exec python3 /victim_server.py {protocol} {port} >/dev/null 2>&1"
    ]
    
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

def build_fortio_cmd(protocol, target_ip, port, qps, conns, duration_sec, out_json):
    if protocol == "http":
        target = f"http://{target_ip}:{port}/"
        return f"fortio load -c {conns} -qps {qps} -t {duration_sec}s -json {out_json} {target}"
    elif protocol == "grpc":
        target = f"{target_ip}:{port}"
        return f"fortio load -grpc -ping -c {conns} -qps {qps} -t {duration_sec}s -json {out_json} {target}"
    elif protocol == "tcp":
        target = f"tcp://{target_ip}:{port}"
        return f"fortio load -c {conns} -qps {qps} -t {duration_sec}s -json {out_json} {target}"
    elif protocol == "udp":
        target = f"udp://{target_ip}:{port}"
        return f"fortio load -c {conns} -qps {qps} -t {duration_sec}s -json {out_json} {target}"
    else:
        raise ValueError(f"Unsupported protocol: {protocol}")

def extract_fortio_metrics(json_file_path):
    if not os.path.exists(json_file_path) or os.path.getsize(json_file_path) == 0:
        return {"p50_ms": None, "p90_ms": None, "p99_ms": None, "p999_ms": None, "actual_qps": None}
    try:
        with open(json_file_path, "r") as f:
            data = json.load(f)
            
        dur = data.get("DurationHistogram", {})
        p50 = dur.get("Percentiles", [{}])[0].get("Value", 0) * 1000.0  # s -> ms
        
        # Parse percentile list
        p_map = {}
        for item in dur.get("Percentiles", []):
            p_map[str(item.get("Percentile"))] = item.get("Value", 0) * 1000.0
            
        p50_ms = p_map.get("50", 0.0)
        p90_ms = p_map.get("90", 0.0)
        p99_ms = p_map.get("99", 0.0)
        p999_ms = p_map.get("99.9", 0.0)
        actual_qps = data.get("ActualQPS", 0.0)
        
        return {
            "p50_ms": round(p50_ms, 3),
            "p90_ms": round(p90_ms, 3),
            "p99_ms": round(p99_ms, 3),
            "p999_ms": round(p999_ms, 3),
            "actual_qps": round(actual_qps, 2)
        }
    except Exception as e:
        log(f"Error parsing {json_file_path}: {e}")
        return {"p50_ms": None, "p90_ms": None, "p99_ms": None, "p999_ms": None, "actual_qps": None}

def run_matrix_combination(arch, protocol, results_dir, flood_steps):
    port = SHARED_CONFIG["ports"][protocol]
    log(f"Starting Matrix Run | Arch: {arch} | Protocol: {protocol} | Port: {port}")
    
    # Step 1: Infrastructure Setup
    run_cmd("./setup_topology.sh")
    
    if arch == "sidecarless":
        log("Attaching eBPF TC classifiers...")
        run_cmd("./attach_ebpf.sh")
    else:
        log("Detaching eBPF classifiers for Sidecar baseline...")
        run_cmd("pkill -9 -f 'socat' 2>/dev/null || true", check=False)
        for dev in ["veth-att-br", "br-mesh", "veth-vic1-br", "veth-vic2-br", "veth-vic3-br"]:
            run_cmd(f"tc qdisc del dev {dev} clsact 2>/dev/null || true", check=False)
            
    # Step 2: Spawn Containers
    run_cmd("runc kill attacker_container KILL 2>/dev/null || true", check=False)
    run_cmd("runc delete attacker_container 2>/dev/null || true", check=False)
    run_cmd("runc run --bundle attacker_bundle -d attacker_container >/dev/null 2>&1")
    
    for v in SHARED_CONFIG["victims"]:
        v_id = v["id"]
        run_cmd(f"runc kill victim_container_{v_id} KILL 2>/dev/null || true", check=False)
        run_cmd(f"runc delete victim_container_{v_id} 2>/dev/null || true", check=False)
        prepare_victim_bundle(v_id, protocol, port)
        run_cmd(f"runc run --bundle victim_bundle_{v_id} -d victim_container_{v_id} >/dev/null 2>&1")
        
    time.sleep(5)  # Let servers bind
    
    # Step 3: Configure Sidecar if needed
    if arch == "sidecar":
        log("Configuring NAT and Socat proxies for sidecar mode...")
        proxy_port = 8080
        for v in SHARED_CONFIG["victims"]:
            v_id = v["id"]
            ns = f"ns_victim{v_id}"
            veth = f"veth-victim{v_id}"
            proto_flag = "-p udp" if protocol == "udp" else "-p tcp"
            
            # Setup iptables redirect
            run_cmd(f"ip netns exec {ns} iptables -t nat -F PREROUTING 2>/dev/null || true", check=False)
            run_cmd(f"ip netns exec {ns} iptables -t nat -A PREROUTING -i {veth} {proto_flag} --dport {port} -j REDIRECT --to-ports {proxy_port}")
            
            # Launch socat proxy
            if protocol == "udp":
                socat_cmd = f"ip netns exec {ns} taskset -c 0 socat UDP-LISTEN:{proxy_port},fork,reuseaddr UDP:127.0.0.1:{port}"
            else:
                socat_cmd = f"ip netns exec {ns} taskset -c 0 socat TCP-LISTEN:{proxy_port},fork,reuseaddr,retry=5 TCP:127.0.0.1:{port}"
            subprocess.Popen(socat_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
        
    # Step 4: Execute Flood Matrix
    summary_rows = []
    for flood_arg in flood_steps:
        log(f"  --> Flood Level: {flood_arg}")
        
        attacker_procs = []
        if flood_arg != "0":
            hping_flag = "--flood" if flood_arg == "flood" else f"--interval {flood_arg}"
            for c in range(4, 8):
                for _ in range(2):  # 2 parallel workers per core for intense lock contention
                    cmd = f"taskset -c {c} ip netns exec ns_attacker hping3 --udp -p 9999 {hping_flag} 10.0.0.10"
                    proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    attacker_procs.append(proc)
            time.sleep(SHARED_CONFIG["warmup_sec"])
            
        # Run fortio for all 3 victims concurrently
        fortio_procs = []
        out_files = []
        for v in SHARED_CONFIG["victims"]:
            v_id = v["id"]
            ip = v["ip"]
            out_json = os.path.join(results_dir, f"fortio_vic{v_id}_{protocol}_{flood_arg}.json")
            out_files.append((v_id, out_json))
            
            f_cmd = build_fortio_cmd(protocol, ip, port, SHARED_CONFIG["qps"], SHARED_CONFIG["conns"], SHARED_CONFIG["duration_sec"], out_json)
            full_cmd = f"taskset -c 0 {f_cmd}"
            proc = subprocess.Popen(full_cmd, shell=True)
            fortio_procs.append(proc)
            
        for proc in fortio_procs:
            proc.wait()
            
        if flood_arg != "0" and attacker_procs:
            for proc in attacker_procs:
                try:
                    proc.kill()
                except Exception:
                    pass
            run_cmd("pkill -9 -f 'hping3' 2>/dev/null || true", check=False)
            time.sleep(1)
            
        # Parse metrics for summary
        for v_id, json_path in out_files:
            metrics = extract_fortio_metrics(json_path)
            summary_rows.append({
                "timestamp": datetime.now().isoformat(),
                "arch": arch,
                "protocol": protocol,
                "flood_level": flood_arg,
                "victim_id": v_id,
                "p50_ms": metrics["p50_ms"],
                "p90_ms": metrics["p90_ms"],
                "p99_ms": metrics["p99_ms"],
                "p999_ms": metrics["p999_ms"],
                "actual_qps": metrics["actual_qps"],
                "raw_output_path": json_path
            })
        time.sleep(2)
        
    return summary_rows

def print_dry_run_manifest(archs, protocols, flood_steps):
    print("=" * 70)
    print(" UNIFIED MULTI-PROTOCOL TEST MATRIX DRY-RUN MANIFEST")
    print("=" * 70)
    print(f"Architectures: {', '.join(archs)}")
    print(f"Protocols:     {', '.join(protocols)}")
    print(f"Flood Levels:  {', '.join(flood_steps)}")
    print(f"Shared Load:   QPS={SHARED_CONFIG['qps']}, Connections={SHARED_CONFIG['conns']}, Duration={SHARED_CONFIG['duration_sec']}s, Warmup={SHARED_CONFIG['warmup_sec']}s")
    print("-" * 70)
    
    total_runs = 0
    for a in archs:
        for p in protocols:
            port = SHARED_CONFIG["ports"][p]
            for f in flood_steps:
                total_runs += 1
                print(f"Run #{total_runs:03d} | Arch: {a:<12} | Protocol: {p:<5} (Port {port}) | Flood: {f:<6}")
                
    print("-" * 70)
    print(f"Total Combinations to Execute: {total_runs}")
    print(f"Estimated Execution Time: ~{int(total_runs * (SHARED_CONFIG['duration_sec'] + 7) / 60)} minutes")
    print("=" * 70)

def main():
    parser = argparse.ArgumentParser(description="Unified Multi-Protocol Benchmark Matrix")
    parser.add_argument("--arch", choices=["sidecarless", "sidecar", "all"], default="sidecarless", help="Target architecture")
    parser.add_argument("--protocol", choices=["http", "grpc", "tcp", "udp", "all"], default="http", help="Target protocol")
    parser.add_argument("--single-run", choices=SHARED_CONFIG["flood_arr"], default=None, help="Run a single flood level step")
    parser.add_argument("--dry-run", action="store_true", help="Print manifest plan without running")
    args = parser.parse_args()
    
    archs = SHARED_CONFIG["supported_archs"] if args.arch == "all" else [args.arch]
    protocols = SHARED_CONFIG["supported_protocols"] if args.protocol == "all" else [args.protocol]
    flood_steps = [args.single_run] if args.single_run else SHARED_CONFIG["flood_arr"]
    
    if args.dry_run:
        print_dry_run_manifest(archs, protocols, flood_steps)
        sys.exit(0)
        
    if os.geteuid() != 0:
        log("ERROR: Must be run as root.")
        sys.exit(1)
        
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = os.path.join("results", "unified", timestamp)
    os.makedirs(results_dir, exist_ok=True)
    
    csv_summary_path = os.path.join(results_dir, "results_summary.csv")
    fieldnames = ["timestamp", "arch", "protocol", "flood_level", "victim_id", "p50_ms", "p90_ms", "p99_ms", "p999_ms", "actual_qps", "raw_output_path"]
    
    with open(csv_summary_path, "w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
    try:
        for a in archs:
            for p in protocols:
                rows = run_matrix_combination(a, p, results_dir, flood_steps)
                with open(csv_summary_path, "a", newline="") as csvfile:
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    for r in rows:
                        writer.writerow(r)
    finally:
        cleanup()
        log(f"Experiment finished. Results saved to {results_dir}")

if __name__ == "__main__":
    main()
