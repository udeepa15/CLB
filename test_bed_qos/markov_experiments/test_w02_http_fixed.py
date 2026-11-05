#!/usr/bin/env python3
"""
test_w02_http_fixed.py — Workload 02 HTTP Contention Benchmark on Port 80.
Executes 1 trial for qos_tiered, qos_dynamic, and qos_dtmc_predictive.
"""

import os
import sys
import time
import json
import threading
import subprocess

CLB_DIR = "/home/udeepa/CLB/test_bed_qos"
MARKOV_DIR = "/home/udeepa/CLB/test_bed_qos/markov_experiments"
CONTROLLER_MARKOV = os.path.join(MARKOV_DIR, "qos_controller_markov.py")


def run_cmd(cmd, check=True):
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=CLB_DIR)
    if check and res.returncode != 0:
        print(f"Error executing {cmd}:\nStderr: {res.stderr}")
    return res


def wait_for_victim_http(timeout=10):
    start = time.time()
    while time.time() - start < timeout:
        res = subprocess.run("ip netns exec ns_victim1 curl -s -m 1 http://10.0.0.10:80/", shell=True, capture_output=True)
        if res.returncode == 0:
            return True
        time.sleep(0.5)
    print("WARNING: Victim HTTP server timeout!")
    return False


class HTTPBurstyAttackerThread(threading.Thread):
    def __init__(self, target_url="http://10.0.0.10:80/", interval_sec=5.0):
        super().__init__()
        self.target_url = target_url
        self.interval_sec = interval_sec
        self.stop_event = threading.Event()
        self.proc = None

    def _start_flood(self, is_high):
        if self.proc:
            try:
                self.proc.terminate()
            except Exception:
                pass
        if is_high:
            # High HTTP flood from ns_attacker: 16 connections, max QPS on port 80
            cmd = f"ip netns exec ns_attacker taskset -c 0,1 fortio load -c 16 -qps 0 -t 5s {self.target_url}"
        else:
            # Low background load: 2 connections, 20 QPS
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
            except Exception:
                pass


def setup_victim():
    bundle_name = f"{CLB_DIR}/victim_bundle_1"
    victim_tmpl = f"{CLB_DIR}/victim_bundle"
    run_cmd(f"rm -rf {bundle_name} && cp -r {victim_tmpl} {bundle_name}")
    config_path = f"{bundle_name}/config.json"
    with open(config_path) as f:
        config = json.load(f)
    for ns in config["linux"]["namespaces"]:
        if ns["type"] == "network":
            ns["path"] = "/var/run/netns/ns_victim1"
    if "resources" not in config["linux"]:
        config["linux"]["resources"] = {}
    config["linux"]["resources"]["cpu"] = {"cpus": "0-1"}
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
    run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)
    run_cmd(f"runc run --bundle {CLB_DIR}/victim_bundle_1 -d victim_container_1")
    wait_for_victim_http()


def parse_fortio_json(json_file):
    if not os.path.exists(json_file):
        return {"p50_ms": 0.0, "p90_ms": 0.0, "p99_ms": 0.0, "p999_ms": 0.0, "actual_qps": 0.0}
    try:
        with open(json_file, "r") as f:
            data = json.load(f)
        dur_hist = data.get("DurationHistogram", {})
        actual_qps = data.get("ActualQPS", 0.0)
        percentiles = {}
        for p in dur_hist.get("Percentiles", []):
            percentiles[float(p["Percentile"])] = p["Value"] * 1000.0
        return {
            "p50_ms": round(percentiles.get(50.0, 0.0), 3),
            "p90_ms": round(percentiles.get(90.0, 0.0), 3),
            "p99_ms": round(percentiles.get(99.0, 0.0), 3),
            "p999_ms": round(percentiles.get(99.9, 0.0), 3),
            "actual_qps": round(actual_qps, 2)
        }
    except Exception:
        return {"p50_ms": 0.0, "p90_ms": 0.0, "p99_ms": 0.0, "p999_ms": 0.0, "actual_qps": 0.0}


def run_arm(arm_name):
    print(f"\n------------------------------------------------")
    print(f"  Testing Arm: {arm_name} (Port 80 HTTP Flood)")
    print(f"------------------------------------------------")

    run_cmd(f"{CLB_DIR}/setup_topology.sh")
    
    ctrl_proc = None
    if arm_name == "qos_tiered":
        run_cmd(f"{CLB_DIR}/attach_ebpf.sh qos_tiered")
    elif arm_name == "qos_dynamic":
        run_cmd(f"{CLB_DIR}/attach_ebpf.sh qos_dynamic")
        ctrl_log = f"/tmp/ctrl_dynamic_{arm_name}.jsonl"
        ctrl_proc = subprocess.Popen(["python3", CONTROLLER_MARKOV, "--log-file", ctrl_log, "--interval", "0.2"])
    else:  # qos_dtmc_predictive
        run_cmd(f"{CLB_DIR}/attach_ebpf.sh qos_dynamic")
        ctrl_log = f"/tmp/ctrl_dtmc_{arm_name}.jsonl"
        ctrl_proc = subprocess.Popen(["python3", CONTROLLER_MARKOV, "--log-file", ctrl_log, "--interval", "0.1"])

    time.sleep(1.0)
    setup_victim()

    # Start HTTP attacker
    att_thread = HTTPBurstyAttackerThread("http://10.0.0.10:80/")
    att_thread.start()
    time.sleep(2.0)

    # Run Fortio test on victim from ns_victim1 (50 QPS, 15s) on port 80
    out_json = f"/tmp/fortio_w02_port80_{arm_name}.json"
    run_cmd(f"ip netns exec ns_victim1 taskset -c 0 fortio load -c 2 -qps 50 -t 15s -json {out_json} http://10.0.0.10:80/", check=False)

    att_thread.stop()
    if ctrl_proc:
        ctrl_proc.kill()
    run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
    run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)

    metrics = parse_fortio_json(out_json)
    print(f"Result for {arm_name}: P50={metrics['p50_ms']}ms, P90={metrics['p90_ms']}ms, P99={metrics['p99_ms']}ms, P99.9={metrics['p999_ms']}ms (QPS={metrics['actual_qps']})")
    return metrics


def main():
    if os.geteuid() != 0:
        print("ERROR: Must run as root!")
        sys.exit(1)

    print("=== WORKLOAD 02 HTTP FLOOD CONTENTION BENCHMARK (FIXED WIRING) ===")
    m1 = run_arm("qos_tiered")
    m2 = run_arm("qos_dynamic")
    m3 = run_arm("qos_dtmc_predictive")

    print("\n=================================================")
    print("  FINAL COMPARISON SUMMARY (PORT 80 HTTP FLOOD)")
    print("=================================================")
    print(f"qos_tiered          -> P99: {m1['p99_ms']} ms (P50: {m1['p50_ms']} ms)")
    print(f"qos_dynamic         -> P99: {m2['p99_ms']} ms (P50: {m2['p50_ms']} ms)")
    print(f"qos_dtmc_predictive -> P99: {m3['p99_ms']} ms (P50: {m3['p50_ms']} ms)")


if __name__ == "__main__":
    main()
