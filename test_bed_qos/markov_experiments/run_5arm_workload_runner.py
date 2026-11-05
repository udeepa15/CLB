#!/usr/bin/env python3
"""
run_5arm_workload_runner.py — Unified 5-Arm Workload Runner across Workloads 01..06.

Executes all 5 verified architectural arms:
1. sidecarless_baseline (Baseline eBPF mesh router, no QoS maps)
2. sidecar              (Proxy reference: socat reverse proxy in victim netns)
3. qos_tiered           (Static Priority Tiering via eBPF maps, zero controller)
4. qos_dynamic          (Dynamic Stackelberg rate limiting controller on CPU 3)
5. qos_dtmc_predictive  (Predictive DTMC Markov controller on CPU 3)

All raw Fortio/client JSON outputs and controller JSONL logs are stored in:
~/CLB/test_bed_qos/markov_experiments/results/workload_XX/raw/
"""

import os
import sys
import time
import json
import random
import signal
import socket
import argparse
import subprocess
from datetime import datetime

MARKOV_DIR = os.path.dirname(os.path.abspath(__file__))
QOS_DIR = os.path.dirname(MARKOV_DIR)
CLB_DIR = os.path.dirname(QOS_DIR)
RESULTS_DIR = os.path.join(MARKOV_DIR, "results")

sys.path.append(os.path.join(QOS_DIR, "workloads", "shared"))
from workload_lib import SHARED_CONFIG, BurstyAttackerThread, RampingAttackerThread, log, run_cmd, wait_for_port, parse_fortio_json

DYNAMIC_CONTROLLER_SCRIPT = os.path.join(QOS_DIR, "qos_controller.py")
MARKOV_CONTROLLER_SCRIPT = os.path.join(MARKOV_DIR, "qos_controller_markov.py")
W06_DIR = os.path.join(QOS_DIR, "workloads", "06_retry_storm")


def lock_cpu_environment():
    """Ensure CPU frequency is locked to performance and C-states disabled."""
    setup_env = os.path.join(CLB_DIR, "test_bed", "setup_env.sh")
    if os.path.exists(setup_env):
        log("Locking CPU environment via setup_env.sh (performance governor, C-states disabled)...")
        run_cmd(f"bash {setup_env}", check=False)


def prepare_victim_bundle(victim_id, protocol="http", port=8080, custom_server_script=None):
    """Prepares victim container bundle."""
    bundle_name = os.path.join(QOS_DIR, f"victim_bundle_{victim_id}")
    victim_tmpl = os.path.join(QOS_DIR, "victim_bundle")
    run_cmd(f"rm -rf {bundle_name} && cp -r {victim_tmpl} {bundle_name}")

    if custom_server_script and os.path.exists(custom_server_script):
        run_cmd(f"cp {custom_server_script} {bundle_name}/rootfs/victim_server.py")

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


def setup_arm_environment(arch, trial_prefix, raw_dir):
    """
    Configures eBPF / proxy / controller environment for a specific architectural arm.
    Returns controller process (if any).
    """
    run_cmd(f"{os.path.join(QOS_DIR, 'setup_topology.sh')}", cwd=QOS_DIR)

    ctrl_proc = None

    if arch in ["sidecarless", "sidecarless_baseline"]:
        run_cmd(f"{os.path.join(QOS_DIR, 'attach_ebpf.sh')} sidecarless", cwd=QOS_DIR)

    elif arch == "qos_tiered":
        run_cmd(f"{os.path.join(QOS_DIR, 'attach_ebpf.sh')} qos_tiered", cwd=QOS_DIR)

    elif arch == "qos_dynamic":
        run_cmd(f"{os.path.join(QOS_DIR, 'attach_ebpf.sh')} qos_dynamic", cwd=QOS_DIR)
        ctrl_log = os.path.join(raw_dir, f"qos_controller_{trial_prefix}.jsonl")
        # Pin controller to CPU 3
        ctrl_proc = subprocess.Popen(["taskset", "-c", "3", "python3", DYNAMIC_CONTROLLER_SCRIPT, "--log-file", ctrl_log, "--interval", "0.2"])
        time.sleep(1)

    elif arch == "qos_dtmc_predictive":
        run_cmd(f"{os.path.join(QOS_DIR, 'attach_ebpf.sh')} qos_dynamic", cwd=QOS_DIR)
        ctrl_log = os.path.join(raw_dir, f"qos_controller_{trial_prefix}.jsonl")
        # Pin controller to CPU 3
        ctrl_proc = subprocess.Popen(["taskset", "-c", "3", "python3", MARKOV_CONTROLLER_SCRIPT, "--log-file", ctrl_log, "--interval", "0.1"])
        time.sleep(1)

    elif arch == "sidecar":
        # Detach eBPF filters, proxy via socat
        for dev in ["veth-att-br", "br-mesh", "veth-vic1-br", "veth-vic2-br", "veth-vic3-br"]:
            run_cmd(f"tc qdisc del dev {dev} clsact 2>/dev/null || true", check=False)

    return ctrl_proc


def setup_sidecar_proxies(victim_ids, target_port=8081):
    """Starts socat reverse proxy in victim netns for sidecar arm (pinned to CPU 0 as in test_bed)."""
    run_cmd("pkill -9 -f 'socat' 2>/dev/null || true", check=False)
    for v_id in victim_ids:
        socat_cmd = f"nsenter --net=/var/run/netns/ns_victim{v_id} taskset -c 0 socat TCP-LISTEN:8080,fork,reuseaddr,retry=5 TCP:127.0.0.1:{target_port} >/dev/null 2>&1 &"
        run_cmd(socat_cmd, check=False)
    time.sleep(1)


def cleanup_trial(ctrl_proc, victim_ids):
    """Cleans up trial processes and containers."""
    if ctrl_proc:
        ctrl_proc.kill()
        try:
            ctrl_proc.wait(timeout=2)
        except Exception:
            pass
    run_cmd("pkill -9 hping3 2>/dev/null || true", check=False)
    run_cmd("pkill -9 -f 'socat' 2>/dev/null || true", check=False)
    for v_id in victim_ids:
        run_cmd(f"runc kill victim_container_{v_id} KILL 2>/dev/null || true", check=False)
        run_cmd(f"runc delete victim_container_{v_id} 2>/dev/null || true", check=False)


def start_hping(level, target_ip="10.0.0.10", port=8080):
    if level == "0":
        return None
    if level == "flood":
        cmd = f"ip netns exec ns_attacker hping3 --udp -p {port} --flood {target_ip}"
    else:
        cmd = f"ip netns exec ns_attacker hping3 --udp -p {port} -i {level} {target_ip}"
    proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.5)
    return proc


def run_workload_01(arms, reps):
    """Workload 01: Steady State Baseline across flood levels."""
    log("\n=======================================================")
    log(f"=== Executing Workload 01 (Steady State Baseline) across {len(arms)} Arms ===")
    log("=======================================================")
    raw_dir = os.path.join(RESULTS_DIR, "workload_01", "raw")
    os.makedirs(raw_dir, exist_ok=True)

    flood_levels = ["0", "u200", "u20", "u2", "u1", "flood"]

    for arm in arms:
        for fl in flood_levels:
            for rep in range(1, reps + 1):
                trial_id = f"{arm}_flood_{fl}_rep_{rep}"
                log(f"[Workload 01] [{arm}] Level={fl} Rep={rep}")

                ctrl_proc = setup_arm_environment(arm, trial_id, raw_dir)
                victim_port = 8081 if arm == "sidecar" else 8080

                prepare_victim_bundle(1, "http", victim_port)
                run_cmd(f"runc run --bundle {os.path.join(QOS_DIR, 'victim_bundle_1')} -d victim_container_1 >/dev/null 2>&1", check=False)

                if arm == "sidecar":
                    setup_sidecar_proxies([1], target_port=8081)

                if not wait_for_port("10.0.0.10", 8080):
                    log("ERROR: Port 8080 not ready!")
                    cleanup_trial(ctrl_proc, [1])
                    continue

                att_proc = start_hping(fl)
                time.sleep(1.5)

                out_json = os.path.join(raw_dir, f"fortio_{trial_id}.json")
                f_cmd = f"taskset -c 0 fortio load -c 2 -qps 50 -t 10s -json {out_json} http://10.0.0.10:8080/"
                subprocess.run(f_cmd, shell=True, capture_output=True, text=True)

                if att_proc:
                    att_proc.kill()
                cleanup_trial(ctrl_proc, [1])


def run_workload_02(arms, reps):
    """Workload 02: Bursty Oscillating Attacker Traffic."""
    log("\n=======================================================")
    log(f"=== Executing Workload 02 (Bursty Oscillating) across {len(arms)} Arms ===")
    log("=======================================================")
    raw_dir = os.path.join(RESULTS_DIR, "workload_02", "raw")
    os.makedirs(raw_dir, exist_ok=True)

    for arm in arms:
        for rep in range(1, reps + 1):
            trial_id = f"{arm}_bursty_rep_{rep}"
            log(f"[Workload 02] [{arm}] Rep={rep}")

            ctrl_proc = setup_arm_environment(arm, trial_id, raw_dir)
            victim_port = 8081 if arm == "sidecar" else 8080

            prepare_victim_bundle(1, "http", victim_port)
            run_cmd(f"runc run --bundle {os.path.join(QOS_DIR, 'victim_bundle_1')} -d victim_container_1 >/dev/null 2>&1", check=False)

            if arm == "sidecar":
                setup_sidecar_proxies([1], target_port=8081)

            if not wait_for_port("10.0.0.10", 8080):
                cleanup_trial(ctrl_proc, [1])
                continue

            att_thread = BurstyAttackerThread("10.0.0.10", 8080, level1="u200", level2="flood", interval_sec=5.0)
            att_thread.start()
            time.sleep(1.5)

            out_json = os.path.join(raw_dir, f"fortio_{trial_id}.json")
            f_cmd = f"taskset -c 0 fortio load -c 2 -qps 50 -t 20s -json {out_json} http://10.0.0.10:8080/"
            subprocess.run(f_cmd, shell=True, capture_output=True, text=True)

            att_thread.stop()
            cleanup_trial(ctrl_proc, [1])


def run_workload_03(arms, reps):
    """Workload 03: Ramping Escalation Attacker Traffic."""
    log("\n=======================================================")
    log(f"=== Executing Workload 03 (Ramping Escalation) across {len(arms)} Arms ===")
    log("=======================================================")
    raw_dir = os.path.join(RESULTS_DIR, "workload_03", "raw")
    os.makedirs(raw_dir, exist_ok=True)

    steps = ["u500", "u200", "u50", "u20", "u5", "u2", "flood"]

    for arm in arms:
        for rep in range(1, reps + 1):
            trial_id = f"{arm}_ramping_rep_{rep}"
            log(f"[Workload 03] [{arm}] Rep={rep}")

            ctrl_proc = setup_arm_environment(arm, trial_id, raw_dir)
            victim_port = 8081 if arm == "sidecar" else 8080

            prepare_victim_bundle(1, "http", victim_port)
            run_cmd(f"runc run --bundle {os.path.join(QOS_DIR, 'victim_bundle_1')} -d victim_container_1 >/dev/null 2>&1", check=False)

            if arm == "sidecar":
                setup_sidecar_proxies([1], target_port=8081)

            if not wait_for_port("10.0.0.10", 8080):
                cleanup_trial(ctrl_proc, [1])
                continue

            att_thread = RampingAttackerThread("10.0.0.10", 8080, steps=steps, step_duration=3.0)
            att_thread.start()
            time.sleep(1.5)

            out_json = os.path.join(raw_dir, f"fortio_{trial_id}.json")
            f_cmd = f"taskset -c 0 fortio load -c 2 -qps 50 -t 21s -json {out_json} http://10.0.0.10:8080/"
            subprocess.run(f_cmd, shell=True, capture_output=True, text=True)

            att_thread.stop()
            cleanup_trial(ctrl_proc, [1])


def run_workload_04(arms, reps):
    """Workload 04: Flash Crowd Demand Surge (50 QPS -> 500 QPS)."""
    log("\n=======================================================")
    log(f"=== Executing Workload 04 (Flash Crowd Demand Surge) across {len(arms)} Arms ===")
    log("=======================================================")
    raw_dir = os.path.join(RESULTS_DIR, "workload_04", "raw")
    os.makedirs(raw_dir, exist_ok=True)

    for arm in arms:
        for rep in range(1, reps + 1):
            trial_id = f"{arm}_flash_rep_{rep}"
            log(f"[Workload 04] [{arm}] Rep={rep}")

            ctrl_proc = setup_arm_environment(arm, trial_id, raw_dir)
            victim_port = 8081 if arm == "sidecar" else 8080

            prepare_victim_bundle(1, "http", victim_port)
            run_cmd(f"runc run --bundle {os.path.join(QOS_DIR, 'victim_bundle_1')} -d victim_container_1 >/dev/null 2>&1", check=False)

            if arm == "sidecar":
                setup_sidecar_proxies([1], target_port=8081)

            if not wait_for_port("10.0.0.10", 8080):
                cleanup_trial(ctrl_proc, [1])
                continue

            att_proc = start_hping("u20")
            time.sleep(1.5)

            # Phase 1: Normal load (50 QPS, 10s)
            out_p1 = os.path.join(raw_dir, f"fortio_{trial_id}_p1.json")
            f_cmd_p1 = f"taskset -c 0 fortio load -c 2 -qps 50 -t 10s -json {out_p1} http://10.0.0.10:8080/"
            subprocess.run(f_cmd_p1, shell=True, capture_output=True, text=True)

            # Phase 2: Flash crowd surge (500 QPS, 10s)
            out_p2 = os.path.join(raw_dir, f"fortio_{trial_id}_p2.json")
            f_cmd_p2 = f"taskset -c 0 fortio load -c 8 -qps 500 -t 10s -json {out_p2} http://10.0.0.10:8080/"
            subprocess.run(f_cmd_p2, shell=True, capture_output=True, text=True)

            if att_proc:
                att_proc.kill()
            cleanup_trial(ctrl_proc, [1])


def run_workload_05(arms, reps):
    """Workload 05: Multi-Tenant Mix (Tenant 1 50 QPS vs Tenant 2 200 QPS)."""
    log("\n=======================================================")
    log(f"=== Executing Workload 05 (Multi-Tenant Isolation) across {len(arms)} Arms ===")
    log("=======================================================")
    raw_dir = os.path.join(RESULTS_DIR, "workload_05", "raw")
    os.makedirs(raw_dir, exist_ok=True)

    for arm in arms:
        for rep in range(1, reps + 1):
            trial_id = f"{arm}_multitenant_rep_{rep}"
            log(f"[Workload 05] [{arm}] Rep={rep}")

            ctrl_proc = setup_arm_environment(arm, trial_id, raw_dir)
            v_port = 8081 if arm == "sidecar" else 8080

            for v_id in [1, 2]:
                prepare_victim_bundle(v_id, "http", v_port)
                run_cmd(f"runc run --bundle {os.path.join(QOS_DIR, f'victim_bundle_{v_id}')} -d victim_container_{v_id} >/dev/null 2>&1", check=False)

            if arm == "sidecar":
                setup_sidecar_proxies([1, 2], target_port=8081)

            wait_for_port("10.0.0.10", 8080)
            wait_for_port("10.0.0.11", 8080)

            att_proc = start_hping("u20")
            time.sleep(1.5)

            out_v1 = os.path.join(raw_dir, f"fortio_{trial_id}_vic1.json")
            out_v2 = os.path.join(raw_dir, f"fortio_{trial_id}_vic2.json")

            cmd1 = f"taskset -c 0 fortio load -c 2 -qps 50 -t 15s -json {out_v1} http://10.0.0.10:8080/"
            cmd2 = f"taskset -c 1 fortio load -c 4 -qps 200 -t 15s -json {out_v2} http://10.0.0.11:8080/"

            p1 = subprocess.Popen(cmd1, shell=True)
            p2 = subprocess.Popen(cmd2, shell=True)
            p1.wait()
            p2.wait()

            if att_proc:
                att_proc.kill()
            cleanup_trial(ctrl_proc, [1, 2])


def run_workload_06(arms, reps):
    """Workload 06: Application-Level Retry Storm with victim_server_retry & retry_client."""
    log("\n=======================================================")
    log(f"=== Executing Workload 06 (Retry Storm Amplification) across {len(arms)} Arms ===")
    log("=======================================================")
    raw_dir = os.path.join(RESULTS_DIR, "workload_06", "raw")
    os.makedirs(raw_dir, exist_ok=True)

    victim_retry_py = os.path.join(W06_DIR, "victim_server_retry.py")
    retry_client_py = os.path.join(W06_DIR, "retry_client.py")

    for arm in arms:
        for rep in range(1, reps + 1):
            trial_id = f"{arm}_retry_rep_{rep}"
            log(f"[Workload 06] [{arm}] Rep={rep}")

            ctrl_proc = setup_arm_environment(arm, trial_id, raw_dir)
            victim_port = 8081 if arm == "sidecar" else 8080

            prepare_victim_bundle(1, "http", victim_port, custom_server_script=victim_retry_py)
            run_cmd(f"runc run --bundle {os.path.join(QOS_DIR, 'victim_bundle_1')} -d victim_container_1 >/dev/null 2>&1", check=False)

            if arm == "sidecar":
                setup_sidecar_proxies([1], target_port=8081)

            if not wait_for_port("10.0.0.10", 8080):
                log("ERROR: Port 8080 not ready!")
                cleanup_trial(ctrl_proc, [1])
                continue

            time.sleep(1.0)

            out_json = os.path.join(raw_dir, f"fortio_{trial_id}.json")
            r_cmd = f"taskset -c 0 python3 {retry_client_py} http://10.0.0.10:8080/ --qps 50 --duration 10 --conns 10 --out {out_json}"
            subprocess.run(r_cmd, shell=True, check=True)

            cleanup_trial(ctrl_proc, [1])


def main():
    parser = argparse.ArgumentParser(description="Unified 5-Arm Workload Runner (Workloads 01..06)")
    parser.add_argument("--reps", type=int, default=2, help="Number of repetitions per trial (default=2 for pilot)")
    parser.add_argument("--arms", nargs="+", default=["sidecarless_baseline", "sidecar", "qos_tiered", "qos_dynamic", "qos_dtmc_predictive"],
                        help="Architectural arms to run")
    parser.add_argument("--workloads", nargs="+", type=int, default=[1, 2, 3, 4, 5, 6],
                        help="Workloads to execute (1-6)")
    args = parser.parse_args()

    if os.geteuid() != 0:
        log("ERROR: Must be run as root (sudo python3 run_5arm_workload_runner.py)")
        sys.exit(1)

    lock_cpu_environment()

    log(f"Starting 5-Arm Workload Runner (reps={args.reps}, arms={args.arms}, workloads={args.workloads})...")

    if 1 in args.workloads:
        run_workload_01(args.arms, args.reps)
    if 2 in args.workloads:
        run_workload_02(args.arms, args.reps)
    if 3 in args.workloads:
        run_workload_03(args.arms, args.reps)
    if 4 in args.workloads:
        run_workload_04(args.arms, args.reps)
    if 5 in args.workloads:
        run_workload_05(args.arms, args.reps)
    if 6 in args.workloads:
        run_workload_06(args.arms, args.reps)

    log("\n[RUN COMPLETE] All requested workloads finished successfully!")


if __name__ == "__main__":
    main()
