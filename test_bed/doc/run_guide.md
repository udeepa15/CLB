# Minimalist Namespace Lab: Step-by-Step Server Run Guide

This guide walks you through preparing your Linux server, deploying the namespace topology, building the OCI containers, compiling the eBPF program, running the benchmark suites, and troubleshooting common issues.

---

## Prerequisites & System Requirements

To measure microsecond-level latency and run the eBPF/cgroup v2 constraints, your server must meet the following:
* **Operating System:** Linux (Ubuntu 22.04 LTS or Debian 12 recommended).
* **Kernel Version:** `5.8` or newer (necessary for modern `tc` eBPF direct-action features and pinned maps). Check using:
  ```bash
  uname -r
  ```
* **cgroups Version:** cgroups v2 must be enabled and mounted (standard on modern distributions). Check using:
  ```bash
  mount | grep cgroup2
  ```

---

## Installation of Host Dependencies

Before running any script, install the compiler toolchain, networking tools, and latency measurement libraries on your host server.

### 1. Core Utilities & eBPF Compiler
```bash
sudo apt-get update
sudo apt-get install -y \
  runc \
  jq \
  clang \
  llvm \
  libbpf-dev \
  linux-headers-$(uname -r) \
  bpftrace \
  socat \
  conntrack \
  wget \
  iptables
```

### 2. Install Fortio (Load generator & Latency Profiler)
`fortio` is run from the root host namespace to measure victim service latency.
```bash
# Download and install Fortio deb package
wget https://github.com/fortio/fortio/releases/download/v1.57.0/fortio_1.57.0_amd64.deb
sudo dpkg -i fortio_1.57.0_amd64.deb
rm fortio_1.57.0_amd64.deb
```

---

## Step-by-Step Execution Plan

Always execute the steps in the following order. All networking and bundle operations require root privileges.

### Step 1: Provision the Data Plane
Run the topology script to construct the virtual switch and isolated namespaces:
```bash
sudo ./setup_topology.sh
```

**Verification:**
* Check that namespaces exist:
  ```bash
  ip netns list
  # Expected output: ns_attacker, ns_victim
  ```
* Verify bridge and link states:
  ```bash
  ip link show br-mesh
  # Expected output: ... state UP ...
  ```
* Ping the namespaces from the host bridge:
  ```bash
  ping -c 3 10.0.0.10  # Ping victim namespace
  ping -c 3 10.0.0.20  # Ping attacker namespace
  ```

### Step 2: Build the Container Bundles
Extract the filesystem bundles, compile `wrk2` inside a containerized sandbox, and write OCI configs:
```bash
sudo ./build_runc_bundles.sh
```

**Verification:**
* Verify that the container OCI configurations are correct and specify CPU isolation:
  ```bash
  grep -A 5 "cpu" victim_bundle/config.json
  # Should show: "cpu": { "cpus": "1" }
  grep -A 5 "cpu" attacker_bundle/config.json
  # Should show: "cpu": { "cpus": "2" }
  ```
* Confirm `wrk2` is compiled inside the attacker's rootfs:
  ```bash
  file attacker_bundle/rootfs/usr/bin/wrk2
  # Should show: ELF 64-bit LSB executable...
  ```

### Step 3: Run the Sidecar (Baseline) Experiment
Execute the traditional proxy baseline:
```bash
sudo ./run_experiment_sidecar.sh
```
*Note: This script will run 4 iterations corresponding to 0, 10k, 20k, and 30k RPS background load. Each iteration takes ~35 seconds. The total run time is ~3 minutes.*

### Step 4: Run the Sidecarless (eBPF) Experiment
Execute the eBPF pinned-map experimental matrix:
```bash
sudo ./run_experiment_sidecarless.sh
```
*Note: This script will automatically invoke `./attach_ebpf.sh` to compile the BPF C code and attach the filters before running the matrix.*

**Manual Verification of eBPF Attachment (Optional):**
If you want to manually verify the eBPF program hooks:
```bash
# Check TC ingress/egress filter lists
tc filter show dev veth-vic-br ingress
tc filter show dev veth-vic-br egress

# Verify that the eBPF map is pinned and visible in bpftool
sudo bpftool map list | grep flow_map
```

---

## Aggregating Results into CSV

Both experiments write their metrics into the `results/` folder. You can use the following bash snippet to aggregate the P99 latencies and spinlock contention metrics from all JSON/log files:

```bash
#!/usr/bin/env bash
# aggregate_results.sh: Run this to parse your experiments into the target CSV format.

echo "Architecture,Attacker_RPS,P50_Latency_ms,P90_Latency_ms,P99_Latency_ms,P999_Latency_ms,Context_Switches,Spinlock_Contention"
echo "-------------------------------------------------------------------------------------------------------------------"

for arch in sidecar sidecarless; do
    # Find the latest timestamped folder
    LATEST_DIR=$(ls -td results/$arch/* 2>/dev/null | head -n 1)
    if [ -z "$LATEST_DIR" ]; then continue; fi
    
    for rps in 0 10000 20000 30000; do
        JSON_FILE="$LATEST_DIR/fortio_rps_${rps}.json"
        LOG_FILE="$LATEST_DIR/bpftrace_rps_${rps}.log"
        
        if [ ! -f "$JSON_FILE" ]; then continue; fi
        
        # Parse percentiles from fortio json
        P50=$(jq '.DurationHistogram.Percentiles[] | select(.Percentile == 50) | .Value * 1000' "$JSON_FILE") # convert seconds to ms
        P90=$(jq '.DurationHistogram.Percentiles[] | select(.Percentile == 90) | .Value * 1000' "$JSON_FILE")
        P99=$(jq '.DurationHistogram.Percentiles[] | select(.Percentile == 99) | .Value * 1000' "$JSON_FILE")
        P999=$(jq '.DurationHistogram.Percentiles[] | select(.Percentile == 99.9) | .Value * 1000' "$JSON_FILE")
        
        # Parse counts from bpftrace logs
        CS_COUNT="N/A"
        LOCK_COUNT="0"
        if [ -f "$LOG_FILE" ]; then
            CS_COUNT=$(grep -oP '@context_switches: \K\d+' "$LOG_FILE" || echo "N/A")
            LOCK_COUNT=$(grep -oP '@spinlock_contention_count: \K\d+' "$LOG_FILE" || echo "0")
        fi
        
        printf "%s,%d,%.3f,%.3f,%.3f,%.3f,%s,%s\n" \
            "$arch" "$rps" "$P50" "$P90" "$P99" "$P999" "$CS_COUNT" "$LOCK_COUNT"
    done
done
```

---

## Troubleshooting & FAQ

#### 1. `bpftrace` fails with "Operation not permitted" or "Locked memory limit"
* **Cause:** eBPF requires root privileges and adequate locked memory limits.
* **Solution:** Run the script as `root`. If it still fails, increase the limits on the host terminal before running:
  ```bash
  ulimit -l unlimited
  ```

#### 2. `runc` fails with "cgroups: cpuset not supported" or similar cgroups v2 issues
* **Cause:** The host cgroup setup does not delegate `cpuset` controllers to the root user.
* **Solution:** Confirm cpuset is present in cgroup controllers list:
  ```bash
  cat /sys/fs/cgroup/cgroup.controllers
  # Make sure "cpuset" is listed. If not, mount cgroup v2 properly or ensure systemd is running.
  ```

#### 3. Networking is broken after a failed run or script abort
* **Cause:** The cleanup traps failed to execute.
* **Solution:** Manually reset the network topology using the standalone cleanup hook:
  ```bash
  sudo ./setup_topology.sh cleanup
  ```

#### 4. Compiler fails: `fatal error: 'linux/bpf.h' file not found`
* **Cause:** Kernel headers are missing or not indexed.
* **Solution:** Install kernel headers for your running kernel version:
  ```bash
  sudo apt-get install -y linux-headers-$(uname -r)
  ```
  If clang still cannot find them, ensure they are symlinked under `/usr/include`:
  ```bash
  sudo ln -sf /usr/src/linux-headers-$(uname -r)/include/uapi/linux/bpf.h /usr/include/linux/bpf.h
  ```
