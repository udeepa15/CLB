#!/usr/bin/env python3
"""
test_http_burst_w02.py — Workload 02 HTTP/TCP Flood Contention Benchmark.
Run directly as root: sudo python3 test_http_burst_w02.py
"""

import os
import sys
import time
import json
import threading
import subprocess
from datetime import datetime

CLB_DIR = "/home/udeepa/CLB/test_bed_qos"
sys.path.append(os.path.join(CLB_DIR, "workloads", "shared"))
from workload_lib import run_cmd, wait_for_port, parse_fortio_json

MARKOV_DIR = "/home/udeepa/CLB/test_bed_qos/markov_experiments"
CONTROLLER_MARKOV = os.path.join(MARKOV_DIR, "qos_controller_markov.py")


class HTTPBurstyAttackerThread(threading.Thread):
    """Alternates between low load (20 QPS) and high HTTP flood (unconstrained -qps 0) every 5s."""
    def __init__(self, target_url="http://10.0.0.10:8080/", interval_sec=5.0):
        super().__init__()
        self.target_url = target_url
        self.interval_sec = interval_sec
        self.stop_event = threading.Event()
        self.proc = None

    def _start_flood(self, is_high):
        if self.proc:
            self.proc.kill()
            subprocess.run("sudo pkill -9 fortio 2>/dev/null || true", shell=True)
        if is_high:
            # High HTTP flood from ns_attacker: 16 connections, max QPS
            cmd = f"sudo ip netns exec ns_attacker taskset -c 0,1 fortio load -c 16 -qps 0 -t 5s {self.target_url}"
        else:
            # Low background HTTP load: 2 connections, 20 QPS
            cmd = f"sudo ip netns exec ns_attacker taskset -c 0,1 fortio load -c 2 -qps 20 -t 5s {self.target_url}"
        return subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def run(self):
        is_high = False
        while not self.stop_event.is_set():
            self.proc = self._start_flood(is_high)
            self.stop_event.wait(self.interval_sec)
            is_high = not is_high

    def stop(self):
        self.stop_event.set()
        if self.proc:
            self.proc.kill()
            subprocess.run("sudo pkill -9 fortio 2>/dev/null || true", shell=True)


def prepare_victim_bundle():
    bundle_name = f"{CLB_DIR}/victim_bundle_1"
    victim_tmpl = f"{CLB_DIR}/victim_bundle"
    run_cmd(f"rm -rf {bundle_name} && cp -r {victim_tmpl} {bundle_name}")
    config_path = f"{bundle_name}/config.json"
    run_cmd(f"chmod -R 777 {bundle_name}")
    with open(config_path) as f:
        config = json.load(f)
    for ns in config["linux"]["namespaces"]:
        if ns["type"] == "network":
            ns["path"] = "/var/run/netns/ns_victim1"
    if "resources" not in config["linux"]:
        config["linux"]["resources"] = {}
    config["linux"]["resources"]["cpu"] = {"cpus": "0-1"}
    config["process"]["args"] = ["sh", "-c", "exec python3 /victim_server.py http 8080"]
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)


def run_trial(arm_name):
    print(f"\n==========================================")
    print(f"  RUNNING WORKLOAD 02 (HTTP FLOOD) FOR: {arm_name}")
    print(f"==========================================")

    # 1. Setup network topology and attach eBPF module
    run_cmd(f"{CLB_DIR}/setup_topology.sh", cwd=CLB_DIR)
    if arm_name == "qos_tiered":
        run_cmd(f"{CLB_DIR}/attach_ebpf.sh qos_tiered", cwd=CLB_DIR)
        ctrl_proc = None
    elif arm_name == "qos_dynamic":
        run_cmd(f"{CLB_DIR}/attach_ebpf.sh qos_dynamic", cwd=CLB_DIR)
        ctrl_log = f"/tmp/ctrl_dynamic.jsonl"
        ctrl_proc = subprocess.Popen(["python3", CONTROLLER_MARKOV, "--log-file", ctrl_log, "--interval", "0.2"])
    else:  # qos_dtmc_predictive
        run_cmd(f"{CLB_DIR}/attach_ebpf.sh qos_dynamic", cwd=CLB_DIR)
        ctrl_log = f"/tmp/ctrl_dtmc.jsonl"
        ctrl_proc = subprocess.Popen(["python3", CONTROLLER_MARKOV, "--log-file", ctrl_log, "--interval", "0.1"])

    time.sleep(1.0)

    # 2. Launch victim container
    run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
    run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)
    prepare_victim_bundle()
    run_cmd(f"runc run --bundle {CLB_DIR}/victim_bundle_1 -d victim_container_1", check=False)

    if not wait_for_port("10.0.0.10", 8080):
        print("Victim server port failed to respond!")
        if ctrl_proc:
            ctrl_proc.kill()
        return None

    # 3. Start HTTP Bursty Attacker
    att_thread = HTTPBurstyAttackerThread("http://10.0.0.10:8080/")
    att_thread.start()
    time.sleep(2.0)

    # 4. Measure victim response latency with Fortio (50 QPS, 20s)
    out_json = f"/tmp/fortio_w02_http_{arm_name}.json"
    f_cmd = f"taskset -c 0 fortio load -c 2 -qps 50 -t 20s -json {out_json} http://10.0.0.10:8080/"
    print(f"Measuring victim latency over 20s burst cycles...")
    subprocess.run(f_cmd, shell=True, capture_output=True, text=True)

    # Cleanup
    att_thread.stop()
    if ctrl_proc:
        ctrl_proc.kill()
    run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
    run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)

    metrics = parse_fortio_json(out_json)
    print(f"\n--- {arm_name} Results ---")
    print(f"  P50:   {metrics['p50_ms']} ms")
    print(f"  P90:   {metrics['p90_ms']} ms")
    print(f"  P99:   {metrics['p99_ms']} ms")
    print(f"  P99.9: {metrics['p999_ms']} ms")
    print(f"  QPS:   {metrics['actual_qps']}")
    return metrics


def main():
    res_tiered = run_trial("qos_tiered")
    res_dynamic = run_trial("qos_dynamic")
    res_dtmc = run_trial("qos_dtmc_predictive")

    print("\n==========================================")
    print("  WORKLOAD 02 HTTP FLOOD COMPARISON SUMMARY")
    print("==========================================")
    print(f"qos_tiered          P99: {res_tiered['p99_ms'] if res_tiered else 'N/A'} ms")
    print(f"qos_dynamic         P99: {res_dynamic['p99_ms'] if res_dynamic else 'N/A'} ms")
    print(f"qos_dtmc_predictive P99: {res_dtmc['p99_ms'] if res_dtmc else 'N/A'} ms")


if __name__ == "__main__":
    main()
