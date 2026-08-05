#!/usr/bin/env python3
"""
run_cluster_matrix.py — 10-Node Cluster Benchmark with 1-5 Noisy Nodes.

Protocol Sequence: HTTP -> TCP -> UDP
Architectures: sidecarless | sidecar
Noisy Node Count: 0, 1, 2, 3, 4, 5

Usage:
  sudo python3 run_cluster_matrix.py --arch all --protocol all
  python3 run_cluster_matrix.py --dry-run
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime

# Baseline Parameters for 10-Node Cluster Benchmark
SHARED_CONFIG = {
    "qps": 50,
    "conns": 2,
    "duration_sec": 10,
    "warmup_sec": 2,
    "noisy_node_counts": [0, 1, 2, 3, 4, 5],
    "protocol_sequence": ["http", "tcp", "udp"],
    "supported_archs": ["sidecarless", "sidecar"],
    "ports": {
        "http": 8080,
        "grpc": 8079,
        "tcp": 8078,
        "udp": 8078,
    },
    "num_nodes": 10,
    "victim_ip": "10.0.0.10"
}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def run_cmd(cmd, check=True, shell=True):
    res = subprocess.run(cmd, shell=shell, capture_output=True, text=True)
    if check and res.returncode != 0:
        log(f"Command failed: {cmd}\nStderr: {res.stderr}")
    return res

def cleanup():
    log("Cleaning up active processes and 10-node cluster state...")
    run_cmd("pkill -9 -f 'hping3' 2>/dev/null || true", check=False)
    run_cmd("pkill -9 -f 'socat' 2>/dev/null || true", check=False)
    run_cmd("pkill -9 -f 'fortio server' 2>/dev/null || true", check=False)

    for i in range(1, SHARED_CONFIG["num_nodes"] + 1):
        run_cmd(f"runc kill node_container_{i} KILL 2>/dev/null || true", check=False)
        run_cmd(f"runc delete node_container_{i} 2>/dev/null || true", check=False)
        run_cmd(f"ip netns exec ns_node{i} iptables -t nat -F 2>/dev/null || true", check=False)

    for dev in ["br-mesh"] + [f"veth-node{i}-br" for i in range(1, SHARED_CONFIG["num_nodes"] + 1)]:
        run_cmd(f"tc qdisc del dev {dev} clsact 2>/dev/null || true", check=False)

    run_cmd("rm -f /sys/fs/bpf/tc/globals/flow_map /sys/fs/bpf/ip/globals/flow_map 2>/dev/null || true", check=False)

def prepare_node_bundle(node_id, protocol, port):
    bundle_name = f"victim_bundle_node{node_id}"
    run_cmd(f"rm -rf {bundle_name} && cp -r victim_bundle {bundle_name}")

    config_path = os.path.join(bundle_name, "config.json")
    with open(config_path, "r") as f:
        config = json.load(f)

    # Update netns path & cpuset
    for ns in config["linux"]["namespaces"]:
        if ns["type"] == "network":
            ns["path"] = f"/var/run/netns/ns_node{node_id}"

    if "resources" not in config["linux"]:
        config["linux"]["resources"] = {}
    config["linux"]["resources"]["cpu"] = {"cpus": str(node_id % 8)}

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
        p_map = {}
        for item in dur.get("Percentiles", []):
            p_map[str(item.get("Percentile"))] = item.get("Value", 0) * 1000.0  # s -> ms

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

def run_matrix_combination(arch, protocol, results_dir, noisy_node_counts):
    port = SHARED_CONFIG["ports"][protocol]
    log(f"Starting 10-Node Cluster Benchmark | Arch: {arch} | Protocol: {protocol} | Port: {port}")

    # Step 1: Topology Setup
    run_cmd("./setup_cluster_10nodes.sh")

    if arch == "sidecarless":
        log("Attaching eBPF classifiers to all 10 nodes...")
        run_cmd("./attach_ebpf_10nodes.sh")
    else:
        log("Detaching eBPF classifiers for Sidecar baseline...")
        run_cmd("pkill -9 -f 'socat' 2>/dev/null || true", check=False)
        for dev in ["br-mesh"] + [f"veth-node{i}-br" for i in range(1, SHARED_CONFIG["num_nodes"] + 1)]:
            run_cmd(f"tc qdisc del dev {dev} clsact 2>/dev/null || true", check=False)

    # Step 2: Spawn Node Containers
    for i in range(1, SHARED_CONFIG["num_nodes"] + 1):
        run_cmd(f"runc kill node_container_{i} KILL 2>/dev/null || true", check=False)
        run_cmd(f"runc delete node_container_{i} 2>/dev/null || true", check=False)
        prepare_node_bundle(i, protocol, port)
        run_cmd(f"runc run --bundle victim_bundle_node{i} -d node_container_{i} >/dev/null 2>&1")

    time.sleep(5)  # Allow servers to bind

    # Step 3: Configure Sidecar Proxy if needed
    if arch == "sidecar":
        log("Configuring NAT and Socat proxies across node namespaces...")
        proxy_port = 8080
        for i in range(1, SHARED_CONFIG["num_nodes"] + 1):
            ns = f"ns_node{i}"
            veth = f"veth-node{i}"
            proto_flag = "-p udp" if protocol == "udp" else "-p tcp"

            run_cmd(f"ip netns exec {ns} iptables -t nat -F PREROUTING 2>/dev/null || true", check=False)
            run_cmd(f"ip netns exec {ns} iptables -t nat -A PREROUTING -i {veth} {proto_flag} --dport {port} -j REDIRECT --to-ports {proxy_port}")

            if protocol == "udp":
                socat_cmd = f"ip netns exec {ns} taskset -c 0 socat UDP-LISTEN:{proxy_port},fork,reuseaddr UDP:127.0.0.1:{port}"
            else:
                socat_cmd = f"ip netns exec {ns} taskset -c 0 socat TCP-LISTEN:{proxy_port},fork,reuseaddr,retry=5 TCP:127.0.0.1:{port}"
            subprocess.Popen(socat_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)

    # Step 4: Sweep Noisy Nodes (1 to 5)
    summary_rows = []
    for noisy_count in noisy_node_counts:
        log(f"  --> Active Noisy Nodes Count: {noisy_count}")

        attacker_procs = []
        if noisy_count > 0:
            # Nodes 2 to (noisy_count + 1) act as noisy nodes
            for noisy_idx in range(1, noisy_count + 1):
                node_num = noisy_idx + 1  # ns_node2..ns_node6
                target_ip = SHARED_CONFIG["victim_ip"]
                target_cpus = [2 + (noisy_idx % 6), 3 + (noisy_idx % 5)]

                for c in target_cpus:
                    for _ in range(2):  # Parallel flood workers
                        cmd = f"taskset -c {c} ip netns exec ns_node{node_num} hping3 --udp -p 9999 --flood {target_ip}"
                        proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        attacker_procs.append(proc)

            time.sleep(SHARED_CONFIG["warmup_sec"])

        # Run Fortio Load client against Node 1 (Victim)
        out_json = os.path.join(results_dir, f"fortio_node1_{protocol}_noisy{noisy_count}.json")
        f_cmd = build_fortio_cmd(protocol, SHARED_CONFIG["victim_ip"], port, SHARED_CONFIG["qps"], SHARED_CONFIG["conns"], SHARED_CONFIG["duration_sec"], out_json)
        full_cmd = f"taskset -c 0 {f_cmd}"

        proc = subprocess.Popen(full_cmd, shell=True)
        proc.wait()

        if noisy_count > 0 and attacker_procs:
            for proc in attacker_procs:
                try:
                    proc.kill()
                except Exception:
                    pass
            run_cmd("pkill -9 -f 'hping3' 2>/dev/null || true", check=False)
            time.sleep(1)

        # Parse metrics
        metrics = extract_fortio_metrics(out_json)
        summary_rows.append({
            "timestamp": datetime.now().isoformat(),
            "arch": arch,
            "protocol": protocol,
            "noisy_nodes_count": noisy_count,
            "victim_node": "ns_node1",
            "p50_ms": metrics["p50_ms"],
            "p90_ms": metrics["p90_ms"],
            "p99_ms": metrics["p99_ms"],
            "p999_ms": metrics["p999_ms"],
            "actual_qps": metrics["actual_qps"],
            "raw_output_path": out_json
        })
        time.sleep(2)

    return summary_rows

def print_dry_run_manifest(archs, protocols, noisy_counts):
    print("=" * 70)
    print(" 10-NODE CLUSTER MULTI-PROTOCOL BENCHMARK DRY-RUN MANIFEST")
    print("=" * 70)
    print(f"Architectures: {', '.join(archs)}")
    print(f"Protocol Sequence: {' -> '.join(protocols)}")
    print(f"Noisy Nodes Count: {', '.join(str(c) for c in noisy_counts)}")
    print(f"Cluster Size: {SHARED_CONFIG['num_nodes']} Nodes")
    print(f"Shared Load: QPS={SHARED_CONFIG['qps']}, Connections={SHARED_CONFIG['conns']}, Duration={SHARED_CONFIG['duration_sec']}s")
    print("-" * 70)

    total_runs = 0
    for p in protocols:
        for a in archs:
            for c in noisy_counts:
                total_runs += 1
                print(f"Run #{total_runs:03d} | Protocol: {p:<5} | Arch: {a:<12} | Noisy Nodes: {c}")

    print("-" * 70)
    print(f"Total Combinations to Execute: {total_runs}")
    print(f"Estimated Execution Time: ~{int(total_runs * (SHARED_CONFIG['duration_sec'] + 8) / 60)} minutes")
    print("=" * 70)

def main():
    parser = argparse.ArgumentParser(description="10-Node Cluster Multi-Protocol Benchmark Matrix")
    parser.add_argument("--arch", choices=["sidecarless", "sidecar", "all"], default="all", help="Target architecture")
    parser.add_argument("--protocol", choices=["http", "tcp", "udp", "all"], default="all", help="Target protocol")
    parser.add_argument("--dry-run", action="store_true", help="Print manifest plan without running")
    args = parser.parse_args()

    archs = SHARED_CONFIG["supported_archs"] if args.arch == "all" else [args.arch]
    protocols = SHARED_CONFIG["protocol_sequence"] if args.protocol == "all" else [args.protocol]
    noisy_counts = SHARED_CONFIG["noisy_node_counts"]

    if args.dry_run:
        print_dry_run_manifest(archs, protocols, noisy_counts)
        sys.exit(0)

    if os.geteuid() != 0:
        log("ERROR: Must be run as root.")
        sys.exit(1)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = os.path.join("results", "cluster_10nodes", timestamp)
    os.makedirs(results_dir, exist_ok=True)

    csv_summary_path = os.path.join(results_dir, "results_summary.csv")
    fieldnames = ["timestamp", "arch", "protocol", "noisy_nodes_count", "victim_node", "p50_ms", "p90_ms", "p99_ms", "p999_ms", "actual_qps", "raw_output_path"]

    with open(csv_summary_path, "w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

    try:
        # Run protocols in sequence: HTTP -> TCP -> UDP
        for p in protocols:
            for a in archs:
                rows = run_matrix_combination(a, p, results_dir, noisy_counts)
                with open(csv_summary_path, "a", newline="") as csvfile:
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    for r in rows:
                        writer.writerow(r)
    finally:
        cleanup()
        log(f"10-Node Cluster experiment finished. Results saved to {results_dir}")

if __name__ == "__main__":
    main()
