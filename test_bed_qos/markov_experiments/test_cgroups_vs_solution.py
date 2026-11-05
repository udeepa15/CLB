#!/usr/bin/env python3
"""
test_cgroups_vs_solution.py — Rigorous Comparative Benchmark: Linux Cgroups vs eBPF QoS Solutions.

Evaluates victim tail latency under peak lock contention (u20 flood against Victim 1)
comparing:
1. sidecarless_baseline  (100% unconstrained, standard eBPF mesh)
2. cgroups_80            (80% container CPU quota via Cgroups v2 cpu.max, standard eBPF mesh)
3. cgroups_50            (50% container CPU quota via Cgroups v2 cpu.max, standard eBPF mesh)
4. qos_tiered            (Static priority tiering in eBPF)
5. qos_dynamic           (Dynamic Stackelberg rate limiting controller)
6. qos_dtmc_predictive   (DTMC Markov predictive controller)

Measures:
- Victim 2 (10.0.0.11:8080): Innocent bystander tenant experiencing shared-map spinlock contention
- Victim 1 (10.0.0.10:8080): Direct target of noisy traffic
"""

import os
import sys
import time
import json
import glob
import signal
import subprocess
from datetime import datetime

CLB_DIR = "/home/udeepa/CLB"
QOS_DIR = os.path.join(CLB_DIR, "test_bed_qos")
MARKOV_DIR = os.path.join(QOS_DIR, "markov_experiments")
RESULTS_DIR = os.path.join(MARKOV_DIR, "results", "cgroups_vs_solution")
GRAPHS_DIR = os.path.join(MARKOV_DIR, "graphs")
HISTORICAL_CGROUPS_DIR = os.path.join(CLB_DIR, "test_bed", "results", "cgroups", "20260721_213813")

os.makedirs(RESULTS_DIR, exist_ok=True)
os.makedirs(GRAPHS_DIR, exist_ok=True)

sys.path.append(os.path.join(QOS_DIR, "workloads", "shared"))
from workload_lib import run_cmd, wait_for_port, parse_fortio_json, log

DYNAMIC_CONTROLLER = os.path.join(QOS_DIR, "qos_controller.py")
MARKOV_CONTROLLER = os.path.join(MARKOV_DIR, "qos_controller_markov.py")


def lock_cpu_governor():
    setup_env = os.path.join(CLB_DIR, "test_bed", "setup_env.sh")
    if os.path.exists(setup_env):
        log("Locking CPU environment via setup_env.sh...")
        run_cmd(f"bash {setup_env}", check=False)


def prepare_victim_bundle(victim_id, port=8080, cpu_quota_pct=None):
    bundle_name = os.path.join(QOS_DIR, f"victim_bundle_{victim_id}")
    victim_tmpl = os.path.join(QOS_DIR, "victim_bundle")
    run_cmd(f"rm -rf {bundle_name} && cp -r {victim_tmpl} {bundle_name}")

    config_path = os.path.join(bundle_name, "config.json")
    with open(config_path, "r") as f:
        config = json.load(f)

    for ns in config["linux"]["namespaces"]:
        if ns["type"] == "network":
            ns["path"] = f"/var/run/netns/ns_victim{victim_id}"

    if "resources" not in config["linux"]:
        config["linux"]["resources"] = {}

    cpu_res = {"cpus": str(victim_id)}
    if cpu_quota_pct is not None:
        period = 100000
        quota = int(cpu_quota_pct * 1000)
        cpu_res["period"] = period
        cpu_res["quota"] = quota

    config["linux"]["resources"]["cpu"] = cpu_res
    config["process"]["args"] = ["sh", "-c", f"exec python3 /victim_server.py http {port}"]

    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)


def prepare_attacker_bundle(cpu_quota_pct=None):
    bundle_name = os.path.join(QOS_DIR, "attacker_bundle")
    config_path = os.path.join(bundle_name, "config.json")
    with open(config_path, "r") as f:
        config = json.load(f)

    if "resources" not in config["linux"]:
        config["linux"]["resources"] = {}

    cpu_res = {}
    if cpu_quota_pct is not None:
        period = 100000
        quota = int(cpu_quota_pct * 1000)
        cpu_res["period"] = period
        cpu_res["quota"] = quota
    config["linux"]["resources"]["cpu"] = cpu_res

    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)


def cleanup(ctrl_proc=None):
    if ctrl_proc:
        try:
            ctrl_proc.kill()
            ctrl_proc.wait(timeout=2)
        except Exception:
            pass
    run_cmd("pkill -9 -f 'hping3' 2>/dev/null || true", check=False)
    for cname in ["victim_container_1", "victim_container_2", "victim_container_3", "victim_container_test", "attacker_container"]:
        run_cmd(f"runc kill {cname} KILL 2>/dev/null || true", check=False)
        run_cmd(f"runc delete --force {cname} 2>/dev/null || true", check=False)
    time.sleep(0.8)


def find_cgroup_dir(container_name):
    # Find cgroup v2 directory for container
    matches = glob.glob(f"/sys/fs/cgroup/**/{container_name}", recursive=True)
    return matches[0] if matches else None


def run_arm(arm_name, quota_pct=None):
    log(f"\n=======================================================")
    log(f"  Executing Arm: {arm_name} (Quota: {quota_pct if quota_pct else '100'}%)")
    log(f"=======================================================")

    cleanup()

    # 1. Setup topology
    run_cmd(f"{os.path.join(QOS_DIR, 'setup_topology.sh')}", cwd=QOS_DIR)

    # 2. Attach eBPF & start controller
    ctrl_proc = None
    if arm_name in ["sidecarless_baseline", "cgroups_80", "cgroups_50"]:
        run_cmd(f"{os.path.join(QOS_DIR, 'attach_ebpf.sh')} sidecarless", cwd=QOS_DIR)
    elif arm_name == "qos_tiered":
        run_cmd(f"{os.path.join(QOS_DIR, 'attach_ebpf.sh')} qos_tiered", cwd=QOS_DIR)
    elif arm_name == "qos_dynamic":
        run_cmd(f"{os.path.join(QOS_DIR, 'attach_ebpf.sh')} qos_dynamic", cwd=QOS_DIR)
        ctrl_log = os.path.join(RESULTS_DIR, f"ctrl_{arm_name}.jsonl")
        ctrl_proc = subprocess.Popen(["taskset", "-c", "3", "python3", DYNAMIC_CONTROLLER, "--log-file", ctrl_log, "--interval", "0.2"])
        time.sleep(1)
    elif arm_name == "qos_dtmc_predictive":
        run_cmd(f"{os.path.join(QOS_DIR, 'attach_ebpf.sh')} qos_dynamic", cwd=QOS_DIR)
        ctrl_log = os.path.join(RESULTS_DIR, f"ctrl_{arm_name}.jsonl")
        ctrl_proc = subprocess.Popen(["taskset", "-c", "3", "python3", MARKOV_CONTROLLER, "--log-file", ctrl_log, "--interval", "0.1"])
        time.sleep(1)

    # 3. Setup Attacker and Victim containers
    prepare_attacker_bundle(cpu_quota_pct=quota_pct)
    prepare_victim_bundle(1, port=8080, cpu_quota_pct=quota_pct)
    prepare_victim_bundle(2, port=8080, cpu_quota_pct=quota_pct)

    run_cmd(f"runc run --bundle {os.path.join(QOS_DIR, 'attacker_bundle')} -d attacker_container >/dev/null 2>&1", check=False)
    run_cmd(f"runc run --bundle {os.path.join(QOS_DIR, 'victim_bundle_1')} -d victim_container_1 >/dev/null 2>&1", check=False)
    run_cmd(f"runc run --bundle {os.path.join(QOS_DIR, 'victim_bundle_2')} -d victim_container_2 >/dev/null 2>&1", check=False)

    if not wait_for_port("10.0.0.10", 8080) or not wait_for_port("10.0.0.11", 8080):
        log("ERROR: Victim ports not reachable!")
        cleanup(ctrl_proc)
        return None

    # 4. Start attacker: UDP u20 flood on Victim 1 (peak lock contention)
    log("Starting u20 attacker flood against 10.0.0.10:8080...")
    att_cmd = "ip netns exec ns_attacker taskset -c 4,5,6,7 hping3 --udp -p 8080 -i u20 10.0.0.10"
    att_proc = subprocess.Popen(att_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # If Cgroups arm, place hping3 PID into attacker_container cgroup procs
    if quota_pct is not None:
        time.sleep(0.5)
        att_cg = find_cgroup_dir("attacker_container")
        if att_cg and os.path.exists(os.path.join(att_cg, "cgroup.procs")):
            try:
                # Find hping3 pids
                res = subprocess.run("pgrep -f 'hping3'", shell=True, capture_output=True, text=True)
                pids = res.stdout.strip().split()
                for p in pids:
                    with open(os.path.join(att_cg, "cgroup.procs"), "w") as fp:
                        fp.write(f"{p}\n")
                log(f"Attached hping3 PID(s) {pids} to {att_cg}/cgroup.procs with quota {quota_pct}%")
            except Exception as e:
                log(f"Warning attaching cgroup procs: {e}")

    time.sleep(1.5)

    # 5. Run Fortio against Victim 2 (bystander pod) and Victim 1 (target pod)
    out_vic2 = os.path.join(RESULTS_DIR, f"fortio_vic2_{arm_name}.json")
    out_vic1 = os.path.join(RESULTS_DIR, f"fortio_vic1_{arm_name}.json")

    log("Running Fortio load (50 QPS, 10s) concurrently on Victim 1 and Victim 2...")
    f1_cmd = f"taskset -c 0 fortio load -c 2 -qps 50 -t 10s -json {out_vic1} http://10.0.0.10:8080/"
    f2_cmd = f"taskset -c 0 fortio load -c 2 -qps 50 -t 10s -json {out_vic2} http://10.0.0.11:8080/"

    p1 = subprocess.Popen(f1_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p2 = subprocess.Popen(f2_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    p1.wait()
    p2.wait()

    # 6. Teardown
    att_proc.kill()
    cleanup(ctrl_proc)

    m1 = parse_fortio_json(out_vic1) if os.path.exists(out_vic1) else {}
    m2 = parse_fortio_json(out_vic2) if os.path.exists(out_vic2) else {}

    log(f"[{arm_name}] Vic2 (Bystander) -> P50: {m2.get('p50_ms', 0):.2f}ms | P90: {m2.get('p90_ms', 0):.2f}ms | P99: {m2.get('p99_ms', 0):.2f}ms | P99.9: {m2.get('p999_ms', 0):.2f}ms")
    log(f"[{arm_name}] Vic1 (Target)    -> P50: {m1.get('p50_ms', 0):.2f}ms | P90: {m1.get('p90_ms', 0):.2f}ms | P99: {m1.get('p99_ms', 0):.2f}ms | P99.9: {m1.get('p999_ms', 0):.2f}ms")

    return {"arm": arm_name, "vic1": m1, "vic2": m2}


def main():
    if os.geteuid() != 0:
        print("ERROR: Must run as root (sudo).")
        sys.exit(1)

    lock_cpu_governor()

    arms_config = [
        ("sidecarless_baseline", None),
        ("cgroups_80", 80),
        ("cgroups_50", 50),
        ("qos_tiered", None),
        ("qos_dynamic", None),
        ("qos_dtmc_predictive", None),
    ]

    all_results = {}
    for arm_name, quota in arms_config:
        res = run_arm(arm_name, quota)
        if res:
            all_results[arm_name] = res

    # Save summary JSON
    summary_path = os.path.join(RESULTS_DIR, "cgroups_vs_solution_summary.json")
    with open(summary_path, "w") as f:
        json.dump(all_results, f, indent=2)

    log(f"\nAll experiments complete. Summary saved to {summary_path}")

    # Generate plots
    log("Generating publication-quality comparison plots...")
    plot_script = os.path.join(MARKOV_DIR, "plot_cgroups_vs_solution.py")
    subprocess.run(["python3", plot_script], check=True)


if __name__ == "__main__":
    main()
