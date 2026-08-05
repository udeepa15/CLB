#!/usr/bin/env python3
"""
run_multi_run_matrix.py — N-Repetition Benchmark Matrix & P99 Latency Aggregator.

Executes N repetitions (default 5) for GRPC, TCP, and HTTP workloads across
Sidecarless (eBPF) and Sidecar (Proxy baseline) architectures under varying flood intensity levels.
Generates randomized manifest.json, collects all raw Fortio outputs, computes averaged metrics (P50/P90/P99/P999),
and outputs results_summary_avg.csv.

Usage:
  sudo python3 run_multi_run_matrix.py --reps 5 --protocols grpc tcp http
  python3 run_multi_run_matrix.py --dry-run
"""

import argparse
import csv
import json
import os
import random
import socket
import subprocess
import sys
import time
from datetime import datetime

SEED = 42

SHARED_CONFIG = {
    "qps": 50,
    "conns": 2,
    "duration_sec": 10,
    "warmup_sec": 2,
    "flood_arr": ["0", "u200", "u20", "u2", "u1", "flood"],
    "ports": {
        "http": 8080,
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
        p_map = {}
        for item in dur.get("Percentiles", []):
            p_map[str(item.get("Percentile"))] = item.get("Value", 0) * 1000.0  # s -> ms

        return {
            "p50_ms": round(p_map.get("50", 0.0), 3),
            "p90_ms": round(p_map.get("90", 0.0), 3),
            "p99_ms": round(p_map.get("99", 0.0), 3),
            "p999_ms": round(p_map.get("99.9", 0.0), 3),
            "actual_qps": round(data.get("ActualQPS", 0.0), 2)
        }
    except Exception as e:
        log(f"Error parsing {json_file_path}: {e}")
        return {"p50_ms": None, "p90_ms": None, "p99_ms": None, "p999_ms": None, "actual_qps": None}


def generate_manifest(archs, protocols, flood_levels, reps, results_dir):
    random.seed(SEED)
    manifest = []

    for arch in archs:
        for proto in protocols:
            for level in flood_levels:
                for rep in range(1, reps + 1):
                    manifest.append({
                        "arch": arch,
                        "protocol": proto,
                        "flood_level": level,
                        "rep": rep
                    })

    random.shuffle(manifest)

    manifest_file = os.path.join(results_dir, "manifest.json")
    with open(manifest_file, "w") as f:
        json.dump({"seed": SEED, "reps": reps, "total_trials": len(manifest), "trials": manifest}, f, indent=2)

    log(f"Manifest created at {manifest_file} with {len(manifest)} randomized trials.")
    return manifest


def execute_trial(trial, results_dir):
    arch = trial["arch"]
    proto = trial["protocol"]
    level = trial["flood_level"]
    rep = trial["rep"]
    port = SHARED_CONFIG["ports"][proto]

    prefix = f"{arch}_{proto}_flood_{level}_rep_{rep}"
    log(f"=== Trial: {prefix} ===")

    # 1. Setup Infrastructure
    run_cmd("./setup_topology.sh")

    if arch == "sidecarless":
        run_cmd("./attach_ebpf.sh")
    else:
        run_cmd("pkill -9 -f 'socat' 2>/dev/null || true", check=False)
        for dev in ["veth-att-br", "br-mesh", "veth-vic1-br", "veth-vic2-br", "veth-vic3-br"]:
            run_cmd(f"tc qdisc del dev {dev} clsact 2>/dev/null || true", check=False)

    # 2. Spawn Attacker Container
    run_cmd("runc kill attacker_container KILL 2>/dev/null || true", check=False)
    run_cmd("runc delete attacker_container 2>/dev/null || true", check=False)
    run_cmd("runc run --bundle attacker_bundle -d attacker_container >/dev/null 2>&1")

    # 3. Spawn Victim Containers
    for v in SHARED_CONFIG["victims"]:
        v_id = v["id"]
        run_cmd(f"runc kill victim_container_{v_id} KILL 2>/dev/null || true", check=False)
        run_cmd(f"runc delete victim_container_{v_id} 2>/dev/null || true", check=False)
        prepare_victim_bundle(v_id, proto, port)
        r_res = run_cmd(f"runc run --bundle victim_bundle_{v_id} -d victim_container_{v_id} >/dev/null 2>&1", check=False)
        if r_res.returncode != 0:
            log(f"CRITICAL ERROR: Runc start failed for victim {v_id}: {r_res.stderr}")
            sys.exit(1)

    # 4. Socket Readiness Polling
    for v in SHARED_CONFIG["victims"]:
        ip = v["ip"]
        ready = wait_for_port(ip, port) if proto != "udp" else wait_for_udp(ip, port)
        if not ready:
            log(f"CRITICAL ERROR: Victim {v['id']} failed socket readiness check!")
            sys.exit(1)

    # 5. Sidecar Reverse Proxy setup
    if arch == "sidecar":
        for v in SHARED_CONFIG["victims"]:
            v_id = v["id"]
            socat_cmd = f"nsenter --net=/var/run/netns/ns_victim{v_id} socat TCP-LISTEN:8080,fork,reuseaddr TCP:127.0.0.1:{port} >/dev/null 2>&1 &"
            run_cmd(socat_cmd, check=False)
            run_cmd(f"ip netns exec ns_victim{v_id} iptables -t nat -A PREROUTING -p tcp --dport {port} -j REDIRECT --to-ports 8080", check=False)
        time.sleep(1)

    # 6. Attacker Flood Traffic
    attacker_procs = []
    if level != "0":
        p_arg = "8078" if proto in ["udp", "tcp"] else str(port)
        hping_flag = "--flood" if level == "flood" else f"-i u{level.replace('u', '')}"
        for c in range(4, 8):
            for _ in range(2):
                cmd = f"taskset -c {c} ip netns exec ns_attacker hping3 --udp -p {p_arg} {hping_flag} 10.0.0.10"
                proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                attacker_procs.append(proc)
        time.sleep(SHARED_CONFIG["warmup_sec"])

    # 7. Execute Fortio Workloads for Victims
    fortio_procs = []
    trial_out_files = []
    raw_dir = os.path.join(results_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    for v in SHARED_CONFIG["victims"]:
        v_id = v["id"]
        ip = v["ip"]
        target_port = 8080 if (arch == "sidecar" and proto in ["http", "grpc"]) else port
        out_json = os.path.join(raw_dir, f"fortio_{prefix}_vic{v_id}.json")
        trial_out_files.append((v_id, out_json))

        f_cmd = build_fortio_cmd(proto, ip, target_port, SHARED_CONFIG["qps"], SHARED_CONFIG["conns"], SHARED_CONFIG["duration_sec"], out_json)
        full_cmd = f"taskset -c 0 {f_cmd}"
        proc = subprocess.Popen(full_cmd, shell=True)
        fortio_procs.append(proc)

    for proc in fortio_procs:
        proc.wait()

    # Stop Attackers
    if level != "0" and attacker_procs:
        for proc in attacker_procs:
            try:
                proc.kill()
            except Exception:
                pass
        run_cmd("pkill -9 -f 'hping3' 2>/dev/null || true", check=False)

    # 8. Record Metrics
    trial_rows = []
    for v_id, json_path in trial_out_files:
        metrics = extract_fortio_metrics(json_path)
        if metrics["p99_ms"] is None:
            log(f"CRITICAL ERROR: Fortio run produced empty output at {json_path}!")
            sys.exit(1)

        trial_rows.append({
            "timestamp": datetime.now().isoformat(),
            "arch": arch,
            "protocol": proto,
            "flood_level": level,
            "rep": rep,
            "victim_id": v_id,
            "p50_ms": metrics["p50_ms"],
            "p90_ms": metrics["p90_ms"],
            "p99_ms": metrics["p99_ms"],
            "p999_ms": metrics["p999_ms"],
            "actual_qps": metrics["actual_qps"],
            "raw_output_path": json_path
        })

    time.sleep(1)
    return trial_rows


def aggregate_and_save(all_rows, results_dir):
    # Save Raw Rows CSV
    raw_csv = os.path.join(results_dir, "results_summary_raw.csv")
    fieldnames = ["timestamp", "arch", "protocol", "flood_level", "rep", "victim_id", "p50_ms", "p90_ms", "p99_ms", "p999_ms", "actual_qps", "raw_output_path"]
    with open(raw_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)
    log(f"Raw results saved to {raw_csv}")

    # Aggregate Averaged Metrics per (arch, protocol, flood_level)
    groups = {}
    for r in all_rows:
        key = (r["arch"], r["protocol"], r["flood_level"])
        if key not in groups:
            groups[key] = {"p50": [], "p90": [], "p99": [], "p999": [], "qps": []}
        groups[key]["p50"].append(r["p50_ms"])
        groups[key]["p90"].append(r["p90_ms"])
        groups[key]["p99"].append(r["p99_ms"])
        groups[key]["p999"].append(r["p999_ms"])
        groups[key]["qps"].append(r["actual_qps"])

    avg_rows = []
    for (arch, proto, level), data in groups.items():
        avg_rows.append({
            "arch": arch,
            "protocol": proto,
            "flood_level": level,
            "reps_count": len(data["p99"]),
            "p50_ms": round(sum(data["p50"]) / len(data["p50"]), 3),
            "p90_ms": round(sum(data["p90"]) / len(data["p90"]), 3),
            "p99_ms": round(sum(data["p99"]) / len(data["p99"]), 3),
            "p999_ms": round(sum(data["p999"]) / len(data["p999"]), 3),
            "actual_qps": round(sum(data["qps"]) / len(data["qps"]), 2)
        })

    avg_csv = os.path.join(results_dir, "results_summary_avg.csv")
    avg_fieldnames = ["arch", "protocol", "flood_level", "reps_count", "p50_ms", "p90_ms", "p99_ms", "p999_ms", "actual_qps"]
    with open(avg_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=avg_fieldnames)
        writer.writeheader()
        writer.writerows(avg_rows)
    log(f"Averaged results saved to {avg_csv}")


def main():
    parser = argparse.ArgumentParser(description="Run N-Repetition Benchmark Matrix & Aggregate P99 Latencies")
    parser.add_argument("--reps", type=int, default=5, help="Number of repetitions per test cell (default: 5)")
    parser.add_argument("--protocols", nargs="+", default=["grpc", "tcp", "http"], help="Protocols to run (default: grpc tcp http)")
    parser.add_argument("--archs", nargs="+", default=["sidecarless", "sidecar"], help="Architectures to run (default: sidecarless sidecar)")
    parser.add_argument("--dry-run", action="store_true", help="Print manifest plan without executing")
    args = parser.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = os.path.join("results", f"multi_run_N{args.reps}_{ts}")
    os.makedirs(results_dir, exist_ok=True)

    manifest = generate_manifest(args.archs, args.protocols, SHARED_CONFIG["flood_arr"], args.reps, results_dir)

    if args.dry_run:
        print("\n=== DRY RUN MANIFEST ===")
        print(f"Total Trials: {len(manifest)}")
        print(f"Protocols:    {args.protocols}")
        print(f"Architectures:{args.archs}")
        print(f"Reps/cell:    {args.reps}")
        print(f"Output Dir:   {results_dir}")
        sys.exit(0)

    if os.geteuid() != 0:
        log("ERROR: Benchmark must be run as root.")
        sys.exit(1)

    log(f"=== Starting {args.reps}-Run Multi-Protocol Benchmark Matrix ===")
    all_summary_rows = []

    for idx, trial in enumerate(manifest):
        log(f"\n--- Progress: Trial {idx+1}/{len(manifest)} ---")
        rows = execute_trial(trial, results_dir)
        all_summary_rows.extend(rows)

    aggregate_and_save(all_summary_rows, results_dir)
    log(f"\nBenchmark Matrix Complete! All averaged results stored in {results_dir}")


if __name__ == "__main__":
    main()
