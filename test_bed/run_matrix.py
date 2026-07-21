#!/usr/bin/env python3
import csv
import itertools
import random
import subprocess
import time
import os
import signal
import sys
from datetime import datetime

N_REPS = 2
DURATION = 10
WARMUP = 2

def get_matrix():
    matrix = []
    # 1. Basics
    matrix.append({'isolation': 'none', 'quota': 0, 'period': 100000, 'offset': 0})
    matrix.append({'isolation': 'cpuset', 'quota': 0, 'period': 100000, 'offset': 0})
    matrix.append({'isolation': 'cpuset_irq', 'quota': 0, 'period': 100000, 'offset': 0})
    matrix.append({'isolation': 'sched_rr', 'quota': 0, 'period': 100000, 'offset': 0})
    
    # 2. Quota sweep
    for q in [80, 85, 90, 95, 99]:
        matrix.append({'isolation': 'cpu.max', 'quota': q, 'period': 100000, 'offset': 0})
        
    # 3. Period sweep (at 90% quota)
    for p in [10000, 25000]:
        matrix.append({'isolation': 'cpu.max', 'quota': 90, 'period': p, 'offset': 0})
        
    # 4. Phase offset sweep (at 90% quota, 100ms period)
    for o in [10, 30, 50]:
        matrix.append({'isolation': 'cpu.max', 'quota': 90, 'period': 100000, 'offset': o})
        
    runs = []
    for rep in range(N_REPS):
        for cfg in matrix:
            runs.append({'rep': rep, **cfg})
            
    random.shuffle(runs)
    return runs

def get_cgroup_path(container_name):
    # runc cgroup v2 path under systemd or raw
    res = subprocess.run(["find", "/sys/fs/cgroup", "-name", container_name, "-type", "d"], capture_output=True, text=True)
    paths = res.stdout.strip().split('\n')
    if paths and paths[0]:
        return paths[0]
    return None

def set_irq_affinity(iface, core_bitmask):
    try:
        irqs = subprocess.check_output(f"grep -i {iface} /proc/interrupts | awk '{{print $1}}' | sed 's/://'", shell=True, text=True).strip().split('\n')
        for irq in irqs:
            if irq:
                subprocess.run(f"echo {core_bitmask} > /proc/irq/{irq}/smp_affinity", shell=True)
    except Exception as e:
        print(f"Error setting IRQ affinity: {e}")

def run_experiment(run_id, total_runs, cfg, out_dir):
    print(f"\n--- RUN {run_id + 1} / {total_runs} ---")
    print(f"Config: {cfg}")
    
    run_path = os.path.join(out_dir, f"run_{run_id}")
    os.makedirs(run_path, exist_ok=True)
    
    # Clean up containers from previous run
    subprocess.run("sudo runc kill attacker_container KILL 2>/dev/null", shell=True)
    subprocess.run("sudo runc delete attacker_container 2>/dev/null", shell=True)
    subprocess.run("sudo runc kill victim_container_1 KILL 2>/dev/null", shell=True)
    subprocess.run("sudo runc delete victim_container_1 2>/dev/null", shell=True)
    
    # Spawn containers
    subprocess.run("sudo runc run --bundle attacker_bundle -d attacker_container", shell=True)
    subprocess.run("rm -rf victim_bundle_1 && cp -r victim_bundle victim_bundle_1", shell=True)
    subprocess.run("sed -i 's/ns_victim/ns_victim1/g' victim_bundle_1/config.json", shell=True)
    subprocess.run("sudo runc run --bundle victim_bundle_1 -d victim_container_1", shell=True)
    
    time.sleep(10) # let python server bind
    
    cgroup_path = get_cgroup_path("victim_container_1")
    if not cgroup_path:
        print("ERROR: Could not find victim cgroup!")
        return False
        
    # Apply Isolation
    if cfg['isolation'] == 'cpu.max':
        quota_val = int(cfg['period'] * (cfg['quota'] / 100.0))
        subprocess.run(f"echo '{quota_val} {cfg['period']}' > {cgroup_path}/cpu.max", shell=True)
        
    if cfg['isolation'] in ['cpuset', 'cpuset_irq']:
        # Pin to cores 0 and 1
        subprocess.run(f"echo '0-1' > {cgroup_path}/cpuset.cpus", shell=True)
    
    if cfg['isolation'] == 'cpuset_irq':
        # Steer to core 2 (bitmask 4)
        set_irq_affinity("eno6", "4")
    else:
        # Steer to all cores (bitmask ff)
        set_irq_affinity("eno6", "ff")
        
    if cfg['isolation'] == 'sched_rr':
        # Find pids in cgroup and set SCHED_RR
        pids = subprocess.check_output(f"cat {cgroup_path}/cgroup.procs", shell=True, text=True).strip().split('\n')
        for pid in pids:
            if pid:
                subprocess.run(f"chrt -f -p 99 {pid} 2>/dev/null", shell=True)
                
    # Start pollers
    cgroup_poller = subprocess.Popen(["sudo", "./collect_cgroup_stats.py", cgroup_path, os.path.join(run_path, "cgroup.csv")])
    ebpf_poller = subprocess.Popen(["sudo", "./collect_ebpf_stats.py", os.path.join(run_path, "ebpf.jsonl")])
    
    # Network state pre
    subprocess.run(["sudo", "./collect_network_stats.sh", "eno6", run_path, "pre"])
    
    # Phase alignment
    if cfg['offset'] > 0:
        offset_sec = cfg['offset'] / 1000.0
        # sleep until the next 100ms boundary
        now = time.time()
        boundary = (int(now * 10) + 1) / 10.0
        time.sleep(boundary - now)
        time.sleep(offset_sec)
        
    # Start Attacker
    hping = subprocess.Popen("sudo ip netns exec ns_attacker hping3 --udp -p 9999 -i u20 10.0.0.10 > /dev/null 2>&1", shell=True, preexec_fn=os.setsid)
    
    # Wait for flood to hit
    time.sleep(1)
    
    # Run Fortio
    fortio_cmd = f"fortio load -c 10 -qps 50 -t {DURATION}s -json {run_path}/fortio.json http://10.0.0.10:80/"
    subprocess.run(fortio_cmd, shell=True)
    
    # Stop everything
    subprocess.run("sudo pkill -9 -f 'hping3'", shell=True)
    cgroup_poller.send_signal(signal.SIGINT)
    ebpf_poller.send_signal(signal.SIGINT)
    
    # Network state post
    subprocess.run(["sudo", "./collect_network_stats.sh", "eno6", run_path, "post"])
    
    # Verify outputs
    cgroup_poller.wait()
    ebpf_poller.wait()
    
    valid = True
    for f in ["cgroup.csv", "ebpf.jsonl", "fortio.json"]:
        p = os.path.join(run_path, f)
        if not os.path.exists(p) or os.path.getsize(p) == 0:
            print(f"FAILED: {f} is missing or empty!")
            valid = False
            
    return valid

def main():
    if os.geteuid() != 0:
        print("Must run as root.")
        sys.exit(1)
        
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = f"results/matrix_{timestamp}"
    os.makedirs(out_dir, exist_ok=True)
    
    runs = get_matrix()
    
    # Write manifest
    with open(os.path.join(out_dir, "manifest.csv"), "w") as f:
        writer = csv.DictWriter(f, fieldnames=['run_id', 'rep', 'isolation', 'quota', 'period', 'offset', 'status'])
        writer.writeheader()
        for i, r in enumerate(runs):
            writer.writerow({'run_id': i, **r, 'status': 'PENDING'})
            
    # Run setup
    subprocess.run(["sudo", "./setup_env.sh"])
    subprocess.run(["sudo", "./setup_topology.sh"])
    subprocess.run(["sudo", "./build_runc_bundles.sh"])
    subprocess.run(["sudo", "./attach_ebpf.sh"])
    
    success_count = 0
    try:
        for i, r in enumerate(runs):
            valid = run_experiment(i, len(runs), r, out_dir)
            
            # Update manifest
            status = 'SUCCESS' if valid else 'FAILED'
            if valid: success_count += 1
            
            with open(os.path.join(out_dir, "manifest.csv"), "a") as f:
                f.write(f"{i},{r['rep']},{r['isolation']},{r['quota']},{r['period']},{r['offset']},{status}\n")
                
            time.sleep(5) # cooldown
    finally:
        subprocess.run(["sudo", "./restore_env.sh"])
        subprocess.run(["sudo", "./setup_topology.sh", "clean"])
        print(f"\nCompleted {success_count}/{len(runs)} runs successfully.")
        print(f"Results saved to {out_dir}")

if __name__ == "__main__":
    main()
