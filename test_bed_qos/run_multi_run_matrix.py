#!/usr/bin/env python3
"""
run_multi_run_matrix.py — 4-Architecture N-Repetition Benchmark Runner for test_bed_qos.

Executes N repetitions across 4 architectures:
  1. sidecarless (unmodified eBPF baseline)
  2. sidecar     (unmodified proxy reference)
  3. qos_tiered  (new: static priority-tier eBPF QoS)
  4. qos_dynamic (new: dynamic Stackelberg eBPF rate limiter)

Generates randomized manifest.json, collects all raw Fortio outputs, computes averaged metrics (P50/P90/P99/P999),
and outputs results_summary_avg.csv.

Usage:
  sudo python3 run_multi_run_matrix.py --reps 2 --protocols http --flood-levels 0 u20 flood
  sudo python3 run_multi_run_matrix.py --reps 5 --protocols grpc tcp http
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


def parse_fortio_json(filepath):
    if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
        return None
    try:
        with open(filepath, "r") as f:
            data = json.load(f)

        actual_qps = data.get("ActualQPS", 0.0)

        p50 = p90 = p99 = p999 = None
        for r in data.get("DurationHistogram", {}).get("Percentiles", []):
            pct = r.get("Percentile", 0)
            val_ms = r.get("Value", 0) * 1000.0
            if pct == 50:
                p50 = val_ms
            elif pct == 90:
                p90 = val_ms
            elif pct == 99:
                p99 = val_ms
            elif pct == 99.9:
                p999 = val_ms

        return {
            "actual_qps": actual_qps,
            "p50_ms": p50 if p50 is not None else 0.0,
            "p90_ms": p90 if p90 is not None else 0.0,
            "p99_ms": p99 if p99 is not None else 0.0,
            "p999_ms": p999 if p999 is not None else 0.0,
        }
    except Exception as e:
        log(f"Error parsing Fortio JSON {filepath}: {e}")
        return None


def generate_manifest(arches, protocols, flood_levels, reps, results_dir):
    manifest = []
    trial_id = 1
    for rep in range(1, reps + 1):
        for proto in protocols:
            for arch in arches:
                for level in flood_levels:
                    manifest.append({
                        "trial_id": trial_id,
                        "arch": arch,
                        "protocol": proto,
                        "flood_level": level,
                        "rep": rep
                    })
                    trial_id += 1

    random.seed(SEED)
    random.shuffle(manifest)

    manifest_file = os.path.join(results_dir, "manifest.json")
    with open(manifest_file, "w") as f:
        json.dump(manifest, f, indent=2)

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

    raw_dir = os.path.join(results_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    controller_proc = None
    if arch == "sidecarless":
        run_cmd("./attach_ebpf.sh sidecarless")
    elif arch == "qos_tiered":
        run_cmd("./attach_ebpf.sh qos_tiered")
    elif arch == "qos_dynamic":
        run_cmd("./attach_ebpf.sh qos_dynamic")
        controller_log = os.path.join(raw_dir, f"qos_controller_{prefix}.jsonl")
        controller_proc = subprocess.Popen(["python3", "qos_controller.py", "--log-file", controller_log, "--interval", "0.2"])
        time.sleep(0.5)
    else:  # sidecar
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
            if controller_proc: controller_proc.kill()
            sys.exit(1)

    # 4. Socket Readiness Polling
    for v in SHARED_CONFIG["victims"]:
        ip = v["ip"]
        ready = wait_for_port(ip, port) if proto != "udp" else wait_for_udp(ip, port)
        if not ready:
            log(f"CRITICAL ERROR: Victim {v['id']} failed socket readiness check!")
            if controller_proc: controller_proc.kill()
            sys.exit(1)

    # 5. Sidecar Reverse Proxy setup
    if arch == "sidecar":
        for v in SHARED_CONFIG["victims"]:
            v_id = v["id"]
            target_p = 8080 if proto in ["http", "grpc"] else port
            socat_cmd = f"nsenter --net=/var/run/netns/ns_victim{v_id} socat TCP-LISTEN:8080,fork,reuseaddr TCP:127.0.0.1:{target_p} >/dev/null 2>&1 &"
            run_cmd(socat_cmd, check=False)
            run_cmd(f"ip netns exec ns_victim{v_id} iptables -t nat -A PREROUTING -p tcp --dport {target_p} -j REDIRECT --to-ports 8080", check=False)
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

    # Kill Attacker & Controller
    for proc in attacker_procs:
        proc.kill()
    if controller_proc:
        controller_proc.kill()

    # 8. Clean up containers
    run_cmd("runc kill attacker_container KILL 2>/dev/null || true", check=False)
    run_cmd("runc delete attacker_container 2>/dev/null || true", check=False)
    for v in SHARED_CONFIG["victims"]:
        v_id = v["id"]
        run_cmd(f"runc kill victim_container_{v_id} KILL 2>/dev/null || true", check=False)
        run_cmd(f"runc delete victim_container_{v_id} 2>/dev/null || true", check=False)

    # 9. Parse Results
    rows = []
    for v_id, json_path in trial_out_files:
        res = parse_fortio_json(json_path)
        if res is None:
            log(f"CRITICAL ERROR: Fortio run produced empty output at {json_path}!")
            sys.exit(1)
        rows.append({
            "arch": arch,
            "protocol": proto,
            "flood_level": level,
            "rep": rep,
            "victim_id": v_id,
            "p50_ms": res["p50_ms"],
            "p90_ms": res["p90_ms"],
            "p99_ms": res["p99_ms"],
            "p999_ms": res["p999_ms"],
            "actual_qps": res["actual_qps"]
        })

    return rows


def aggregate_results(raw_rows, results_dir):
    raw_csv = os.path.join(results_dir, "results_summary_raw.csv")
    fieldnames = ["arch", "protocol", "flood_level", "rep", "victim_id", "p50_ms", "p90_ms", "p99_ms", "p999_ms", "actual_qps"]

    with open(raw_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(raw_rows)

    # Cell aggregation (Arch, Proto, Flood)
    cells = {}
    for r in raw_rows:
        key = (r["arch"], r["protocol"], r["flood_level"])
        if key not in cells:
            cells[key] = {"p50": [], "p90": [], "p99": [], "p999": [], "qps": []}
        cells[key]["p50"].append(r["p50_ms"])
        cells[key]["p90"].append(r["p90_ms"])
        cells[key]["p99"].append(r["p99_ms"])
        cells[key]["p999"].append(r["p999_ms"])
        cells[key]["qps"].append(r["actual_qps"])

    avg_csv = os.path.join(results_dir, "results_summary_avg.csv")
    avg_fieldnames = ["arch", "protocol", "flood_level", "reps_count", "p50_ms", "p90_ms", "p99_ms", "p999_ms", "actual_qps"]

    avg_rows = []
    for (arch, proto, level), vals in cells.items():
        n = len(vals["p99"])
        avg_rows.append({
            "arch": arch,
            "protocol": proto,
            "flood_level": level,
            "reps_count": n,
            "p50_ms": round(sum(vals["p50"]) / n, 3),
            "p90_ms": round(sum(vals["p90"]) / n, 3),
            "p99_ms": round(sum(vals["p99"]) / n, 3),
            "p999_ms": round(sum(vals["p999"]) / n, 3),
            "actual_qps": round(sum(vals["qps"]) / n, 2),
        })

    with open(avg_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=avg_fieldnames)
        writer.writeheader()
        writer.writerows(avg_rows)

    log(f"Successfully aggregated {len(raw_rows)} trial results into {avg_csv}")


def main():
    parser = argparse.ArgumentParser(description="4-Architecture Benchmark Matrix Runner for test_bed_qos")
    parser.add_argument("--reps", type=int, default=5, help="Number of repetitions per cell (default: 5)")
    parser.add_argument("--protocols", nargs="+", default=["grpc", "tcp", "http"], help="Protocols to run (default: grpc tcp http)")
    parser.add_argument("--arches", nargs="+", default=["sidecarless", "sidecar", "qos_tiered", "qos_dynamic"], help="Architectures to evaluate")
    parser.add_argument("--flood-levels", nargs="+", default=SHARED_CONFIG["flood_arr"], help="Flood levels to run")
    parser.add_argument("--dry-run", action="store_true", help="Print manifest and exit without running")
    args = parser.parse_args()

    if args.dry_run:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        results_dir = os.path.join("results", f"dry_run_{timestamp}")
        manifest = generate_manifest(args.arches, args.protocols, args.flood_levels, args.reps, results_dir)
        print(json.dumps(manifest[:10], indent=2))
        print(f"... Total {len(manifest)} trials scheduled.")
        sys.exit(0)

    if os.geteuid() != 0:
        log("ERROR: Must be run as root (sudo python3 run_multi_run_matrix.py)")
        sys.exit(1)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = os.path.join("results", f"multi_run_N{args.reps}_{timestamp}")
    os.makedirs(results_dir, exist_ok=True)

    manifest = generate_manifest(args.arches, args.protocols, args.flood_levels, args.reps, results_dir)

    log(f"=== Starting 4-Architecture Matrix Execution ({args.reps} Repetitions, Total {len(manifest)} Trials) ===")

    all_raw_rows = []
    total_trials = len(manifest)

    for idx, trial in enumerate(manifest, 1):
        log(f"\n--- Progress: Trial {idx}/{total_trials} ---")
        rows = execute_trial(trial, results_dir)
        all_raw_rows.extend(rows)

    log("\n=== Benchmark Complete! Aggregating metrics... ===")
    aggregate_results(all_raw_rows, results_dir)


if __name__ == "__main__":
    main()
