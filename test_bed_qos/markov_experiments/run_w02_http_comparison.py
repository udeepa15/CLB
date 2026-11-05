#!/usr/bin/env python3
"""
run_w02_http_comparison.py — Pure Root Benchmark for Workload 02 under HTTP TCP Contention.
Must be run as root: sudo python3 run_w02_http_comparison.py
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
from workload_lib import wait_for_port, parse_fortio_json

MARKOV_DIR = "/home/udeepa/CLB/test_bed_qos/markov_experiments"
CONTROLLER_MARKOV = os.path.join(MARKOV_DIR, "qos_controller_markov.py")


class HTTPBurstyAttackerThread(threading.Thread):
    def __init__(self, target_url="http://10.0.0.10:8080/", interval_sec=5.0):
        super().__init__()
        self.target_url = target_url
        self.interval_sec = interval_sec
        self.stop_event = threading.Event()
        self.proc = None

    def _start_flood(self, is_high):
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=1.0)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        if is_high:
            cmd = f"ip netns exec ns_attacker taskset -c 0,1 fortio load -c 16 -qps 0 -t 5s {self.target_url}"
        else:
            cmd = f"ip netns exec ns_attacker taskset -c 0,1 fortio load -c 2 -qps 20 -t 5s {self.target_url}"
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
            try:
                self.proc.terminate()
                self.proc.wait(timeout=1.0)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass


def setup_victim():
    bundle_name = f"{CLB_DIR}/victim_bundle_1"
    victim_tmpl = f"{CLB_DIR}/victim_bundle"
    subprocess.run(f"rm -rf {bundle_name} && cp -r {victim_tmpl} {bundle_name}", shell=True, check=True)
    config_path = f"{bundle_name}/config.json"
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

    subprocess.run("runc kill victim_container_1 KILL 2>/dev/null || true", shell=True)
    subprocess.run("runc delete victim_container_1 2>/dev/null || true", shell=True)
    subprocess.run(f"runc run --bundle {CLB_DIR}/victim_bundle_1 -d victim_container_1", shell=True, check=True)
    wait_for_port("10.0.0.10", 8080)


def run_arm(arm_name):
    print(f"\n------------------------------------------------")
    print(f"  Testing Arm: {arm_name}")
    print(f"------------------------------------------------")

    subprocess.run(f"{CLB_DIR}/setup_topology.sh", shell=True, cwd=CLB_DIR)
    
    ctrl_proc = None
    if arm_name == "qos_tiered":
        subprocess.run(f"{CLB_DIR}/attach_ebpf.sh qos_tiered", shell=True, cwd=CLB_DIR)
    elif arm_name == "qos_dynamic":
        subprocess.run(f"{CLB_DIR}/attach_ebpf.sh qos_dynamic", shell=True, cwd=CLB_DIR)
        ctrl_log = f"/tmp/ctrl_dynamic_{arm_name}.jsonl"
        ctrl_proc = subprocess.Popen(["python3", CONTROLLER_MARKOV, "--log-file", ctrl_log, "--interval", "0.2"])
    else:  # qos_dtmc_predictive
        subprocess.run(f"{CLB_DIR}/attach_ebpf.sh qos_dynamic", shell=True, cwd=CLB_DIR)
        ctrl_log = f"/tmp/ctrl_dtmc_{arm_name}.jsonl"
        ctrl_proc = subprocess.Popen(["python3", CONTROLLER_MARKOV, "--log-file", ctrl_log, "--interval", "0.1"])

    time.sleep(1.0)
    setup_victim()

    # Start HTTP attacker
    att_thread = HTTPBurstyAttackerThread()
    att_thread.start()
    time.sleep(2.0)

    # Run Fortio test on victim (50 QPS, 15s)
    out_json = f"/tmp/fortio_w02_http_{arm_name}.json"
    subprocess.run(f"taskset -c 0 fortio load -c 2 -qps 50 -t 15s -json {out_json} http://10.0.0.10:8080/", shell=True)

    att_thread.stop()
    if ctrl_proc:
        ctrl_proc.kill()
    subprocess.run("runc kill victim_container_1 KILL 2>/dev/null || true", shell=True)
    subprocess.run("runc delete victim_container_1 2>/dev/null || true", shell=True)

    metrics = parse_fortio_json(out_json)
    print(f"Result for {arm_name}: P50={metrics['p50_ms']}ms, P90={metrics['p90_ms']}ms, P99={metrics['p99_ms']}ms, P99.9={metrics['p999_ms']}ms")
    return metrics


def main():
    print("=== WORKLOAD 02 HTTP FLOOD CONTENTION BENCHMARK ===")
    m1 = run_arm("qos_tiered")
    m2 = run_arm("qos_dynamic")
    m3 = run_arm("qos_dtmc_predictive")

    print("\n=================================================")
    print("  FINAL COMPARISON SUMMARY (WORKLOAD 02 HTTP)")
    print("=================================================")
    print(f"qos_tiered          -> P99: {m1['p99_ms']} ms")
    print(f"qos_dynamic         -> P99: {m2['p99_ms']} ms")
    print(f"qos_dtmc_predictive -> P99: {m3['p99_ms']} ms")


if __name__ == "__main__":
    main()
