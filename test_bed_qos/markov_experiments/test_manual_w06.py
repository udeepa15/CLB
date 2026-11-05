import os, sys, time, json, subprocess

CLB_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
QOS_DIR = os.path.join(CLB_DIR, "test_bed_qos")
sys.path.append(os.path.join(QOS_DIR, "workloads", "shared"))
from workload_lib import run_cmd, wait_for_port, log

W06_DIR = os.path.join(QOS_DIR, "workloads", "06_retry_storm")
MARKOV_DIR = os.path.join(QOS_DIR, "markov_experiments")
CONTROLLER_SCRIPT = os.path.join(MARKOV_DIR, "qos_controller_markov.py")

# 1. Setup topology & ebpf
run_cmd(f"{os.path.join(QOS_DIR, 'setup_topology.sh')}", cwd=QOS_DIR)
run_cmd(f"{os.path.join(QOS_DIR, 'attach_ebpf.sh')} qos_dynamic", cwd=QOS_DIR)

# 2. Pin controller to CPU 3
ctrl_proc = subprocess.Popen(["taskset", "-c", "3", "python3", CONTROLLER_SCRIPT, "--log-file", "/tmp/ctrl_w06_test.jsonl", "--interval", "0.1"])
time.sleep(1)

# 3. Clean and prepare victim bundle with victim_server_retry.py
run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)

bundle_dir = os.path.join(QOS_DIR, "victim_bundle_1")
victim_tmpl = os.path.join(QOS_DIR, "victim_bundle")
run_cmd(f"rm -rf {bundle_dir} && cp -r {victim_tmpl} {bundle_dir}")
run_cmd(f"cp {os.path.join(W06_DIR, 'victim_server_retry.py')} {bundle_dir}/rootfs/victim_server.py")

config_path = os.path.join(bundle_dir, "config.json")
with open(config_path, "r") as f:
    config = json.load(f)

for ns in config["linux"]["namespaces"]:
    if ns["type"] == "network":
        ns["path"] = "/var/run/netns/ns_victim1"

if "resources" not in config["linux"]:
    config["linux"]["resources"] = {}
config["linux"]["resources"]["cpu"] = {"cpus": "1"}

config["process"]["args"] = ["sh", "-c", "exec python3 /victim_server.py http 8080"]

with open(config_path, "w") as f:
    json.dump(config, f, indent=2)

run_cmd(f"runc run --bundle {bundle_dir} -d victim_container_1 >/dev/null 2>&1", check=False)

if not wait_for_port("10.0.0.10", 8080):
    print("ERROR: Port 8080 not reachable")
    ctrl_proc.kill()
    sys.exit(1)

print("Victim server with retry simulation listening on 10.0.0.10:8080!")

# 4. Run retry_client.py pinned to CPU 0
out_json = "/tmp/test_w06_retry_single_rep.json"
cmd = f"taskset -c 0 python3 {os.path.join(W06_DIR, 'retry_client.py')} http://10.0.0.10:8080/ --qps 50 --duration 10 --conns 10 --out {out_json}"
print(f"Running client: {cmd}")
subprocess.run(cmd, shell=True, check=True)

# 5. Clean up
ctrl_proc.kill()
run_cmd("runc kill victim_container_1 KILL 2>/dev/null || true", check=False)
run_cmd("runc delete victim_container_1 2>/dev/null || true", check=False)

# 6. Read and print results
with open(out_json) as f:
    data = json.load(f)
print("\n=== W06 RETRY CLIENT OUTPUT ===")
print(json.dumps(data, indent=2))
