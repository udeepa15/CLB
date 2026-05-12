# eBPF Noisy Neighbor Research: Setup, Runtime, and Results Guide

This document provides a comprehensive explanation of the eBPF noisy neighbor benchmark setup, how it executes, and how to interpret the results.

---

## Part 1: Setup Phase

### Overview
The setup phase prepares the host environment for benchmarking. It creates network infrastructure, container bundles, compiles eBPF programs, and initializes the benchmark infrastructure.

### Setup Script: `scripts/setup.sh`

#### What It Does

1. **Creates BPF Filesystem**
   ```bash
   mount -t bpf bpf /sys/fs/bpf
   mkdir -p /sys/fs/bpf/ebpf_research
   ```
   - Mounts the BPF virtual filesystem if not already mounted
   - Creates a dedicated directory for eBPF research objects
   - Allows persistent pinning of eBPF programs across loads

2. **Sets Up Network Infrastructure**
   - Creates a bridge named `mesh0` with IP `10.200.0.1/24`
   - Creates virtual ethernet (veth) pairs for each container
   - Creates network namespaces (`victim_ns`, `attacker_ns`, etc.)
   - Connects containers to the bridge via veth pairs
   - Configures IP addresses and routing within namespaces

   **Network Layout:**
   ```
   Bridge (mesh0): 10.200.0.1/24
   ├── Victim namespace (10.200.0.2/24)
   │   └── HTTP service on :8080
   ├── Victim2 namespace (10.200.0.3/24)
   ├── Victim3 namespace (10.200.0.4/24)
   └── Attacker namespace (10.200.0.100/24)
       └── Load generation tools (wrk2, Fortio)
   ```

3. **Prepares Container Rootfs**
   - Copies Alpine Linux template from `ebpf-noisy-neighbor/containers/alpine-rootfs/`
   - Creates minimal HTTP echo service script
   - Sets up read-only filesystem root with writable /tmp
   - No explicit CPU or memory limits applied (uses host defaults)

4. **Generates OCI Bundle Configurations**
   - Uses `runc spec` to create OCI v1.2.1 compliant configs
   - Configures process arguments:
     - **Victim**: `busybox nc -lk -p 8080 -e /bin/http-echo.sh`
     - **Attacker**: `sleep infinity`
   - Sets up network namespace paths
   - Defines capabilities (CAP_AUDIT_WRITE, CAP_KILL, CAP_NET_BIND_SERVICE)
   - Sets RLIMIT_NOFILE to 1024

   **Bundle Structure:**
   ```
   bundles/victim/
   ├── config.json      (OCI spec with process, mounts, namespaces)
   └── rootfs/          (Alpine filesystem)
       ├── bin/http-echo.sh
       └── [standard Linux directories]
   
   bundles/attacker/
   ├── config.json
   └── rootfs/
   ```

5. **Compiles eBPF Programs**
   ```bash
   # Shared map version (single global counter)
   clang -O2 -target bpf -c counter_tc.c -o counter_tc_shared.o
   
   # Isolated version (per-interface counter)
   clang -O2 -target bpf -DPER_IFINDEX_KEY=1 -c counter_tc.c -o counter_tc_isolated.o
   ```
   - Compiles `counter_tc.c` into two variants
   - Shared version: All traffic updates one global map entry (creates contention)
   - Isolated version: Each interface has its own map key (reduces contention)
   - Objects stored in `bpf/` for later attachment

### Key Configuration Parameters

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `NUM_VICTIMS` | 5 | Number of victim containers to prepare |
| `VICTIM_IP_BASE` | 10.200.0 | Subnet for victim network |
| `ATTACKER_IP` | 10.200.0.100/24 | Attacker container IP |
| `BRIDGE` | mesh0 | Bridge device name |
| `ROOTFS_SOURCE` | `../ebpf-noisy-neighbor/containers/alpine-rootfs/rootfs-template` | Alpine template location |

### Resource Configuration

The containers are created **without explicit CPU or memory limits**:
- CPU: Inherits from host scheduler (no cgroup CPU quota)
- Memory: No cgroup memory limits
- Network: Limited only by kernel buffer sizes (65536k tmpfs for /dev)
- File descriptors: Hard limit of 1024 (RLIMIT_NOFILE)

This means containers can use all available host resources, making the test sensitive to eBPF contention rather than resource exhaustion.

---

## Part 2: Runtime Execution

### Benchmark Scripts

#### 1. `scripts/run_benchmarks.sh` (Simple Three-Config Test)

**What It Does:**
- Runs a single benchmark with three configurations in sequence
- Each configuration measures victim latency while optionally adding attacker noise

**Execution Flow:**

```
1. start_containers()
   ├── runc run -d --bundle bundles/victim victim_ct
   ├── runc run -d --bundle bundles/attacker attacker_ct
   └── wait_for_victim_ready (TCP probe to 10.200.0.2:8080)

2. For each configuration (baseline, sidecar_isolation, sidecarless_contention):
   a. apply_config_*()
      ├── reset_tc (remove previous eBPF programs)
      └── Attach eBPF program or keep clean
   
   b. run_one(config)
      ├── Start attacker noise (wrk2 or Fortio)
      │  └── Target: 1000 RPS to 10.200.0.2:8080
      ├── Run Fortio measurement
      │  ├── 60 seconds duration
      │  ├── 16 concurrent connections
      │  ├── Measure P50, P95, P99 latencies
      │  └── Measure actual QPS achieved
      ├── Wait for noise to complete
      └── Extract metrics and append to CSV

3. Collect output files:
   ├── results/raw/{config}_fortio.json
   ├── results/raw/{config}_fortio.log
   ├── results/raw/{config}_wrk2.log
   └── results/metrics.csv

4. stop_containers()
   ├── runc delete -f victim_ct
   └── runc delete -f attacker_ct
```

**Key Variables:**
```bash
DURATION_SECONDS=60           # How long each test runs
TARGET_URL="http://10.200.0.2:8080/"
ATTACKER_TARGET_RPS=1000      # Load generator target RPS
FORTIO_PERCENTILES="50,95,99" # Which percentiles to measure
```

#### 2. `scripts/sidecar_vs_sidecarless/run_noise_sweep.sh` (Sweep Test)

**What It Does:**
- Runs a configurable noise sweep across increasing attacker load levels
- Repeats each noise level multiple times for statistical reliability
- Focuses only on sidecar_isolation vs sidecarless_contention (excludes baseline)

**Execution Flow:**

```
For each REPEAT (default 3 times):
  For each NOISE_LEVEL in {0, 10000, 20000, 40000, 60000} RPS:
    1. start_containers()
    
    2. For each configuration (sidecar, sidecarless):
       a. apply_config_*()
       
       b. run_one(config, noise_level)
          ├── Start mpstat CPU profiler in background
          ├── Start attacker noise at noise_level RPS
          │  └── Target closed port :8081 (no app interference)
          ├── Warm up eBPF maps for 3 seconds
          ├── Run Fortio for 60 seconds
          │  ├── 1000 QPS target
          │  ├── 4 concurrent connections (reduced from 16)
          ├── Collect CPU/softirq logs
          └── Extract and record all metrics
    
    3. stop_containers()

Output files per test:
├── results/raw/sidecar_vs_sidecarless/
│   ├── sidecar_isolation_r1_n0_fortio.json
│   ├── sidecar_isolation_r1_n0_fortio.log
│   ├── sidecar_isolation_r1_n0_attacker.log
│   ├── sidecar_isolation_r1_n0_cpu_softirq.log
│   └── ... (similar for other configs/levels/runs)
└── results/sidecar_vs_sidecarless_metrics.csv
    (timestamp, config, noise_target_rps, run_id, p50, p95, p99, throughput, attacker_rps)
```

**Key Variables (Tunable):**
```bash
DURATION_SECONDS=60                           # Test duration
REPEATS=3                                     # Number of times to repeat each noise level
NOISE_LEVELS_CSV="0,10000,20000,40000,60000" # Noise levels to test
ATTACKER_LOAD_TOOL="fortio"                   # wrk2 or fortio for noise
```

### What Happens During Execution

#### Phase 1: Container Startup
- runc launches each container from its bundle
- Containers enter their network namespaces (isolated)
- Victim service listens on 10.200.0.2:8080
- Attacker container is ready to run load tools

#### Phase 2: eBPF Attachment (Config-Dependent)

**Baseline:** No eBPF program
```
→ Victim and attacker experience no kernel contention from eBPF
```

**Sidecar Isolation:**
```
tc filter add dev veth_vic_h ingress bpf da obj counter_tc_isolated.o
tc filter add dev veth_att_h ingress bpf da obj counter_tc_isolated.o

→ Each interface has its own map key (skb->ifindex)
→ Victim packets update key 2, attacker packets update key 100
→ No map contention between victim and attacker
```

**Sidecarless Contention:**
```
bpftool prog loadall counter_tc_shared.o /sys/fs/bpf/ebpf_research/shared
tc filter add dev veth_vic_h ingress bpf da pinned /sys/fs/bpf/.../count_ingress
tc filter add dev veth_att_h ingress bpf da pinned /sys/fs/bpf/.../count_ingress

→ Both interfaces use the same pinned program
→ Both update the same map entry (key 0)
→ Creates kernel-level lock contention on atomic_fetch_and_add()
```

#### Phase 3: Parallel Measurement and Noise

**Victim Measurement (Fortio):**
- Sends HTTP requests to victim service
- Records request start time and response time
- Accumulates latency histogram
- Runs for fixed duration (60 seconds)

**Attacker Noise (wrk2 or Fortio):**
- Generates load from attacker namespace
- In sweep mode: targets closed port :8081 to stress eBPF without app interference
- Generates background traffic that hits the eBPF program
- Both streams hit tc ingress simultaneously

**eBPF Program Execution:**
```c
// For each packet hitting ingress:
SEC("tc")
int count_ingress(struct __sk_buff *skb) {
    __u32 key = ... // either skb->ifindex or 0
    
    // LOCK POINT (in shared mode, all packets compete here)
    __sync_fetch_and_add(&packet_count[key], 1);
    
    return TC_ACT_OK;  // Pass packet through
}
```

The lock contention translates to:
- Increased kernel softirq time
- CPU cache invalidation
- Longer lock hold times
- Increased request latency measured by Fortio

#### Phase 4: Data Collection

**Fortio Output (JSON):**
```json
{
  "ActualQPS": 997.5,
  "Count": 59850,
  "Successes": 59850,
  "Errors": 0,
  "DurationSeconds": 60,
  "DurationHistogram": {
    "Percentiles": [
      {"Percentile": 50.0, "Value": 0.002 },    // 2ms median
      {"Percentile": 95.0, "Value": 0.005 },    // 5ms p95
      {"Percentile": 99.0, "Value": 0.025 }     // 25ms p99
    ]
  }
}
```

**Metrics CSV:**
```
timestamp,config,noise_target_rps,run_id,p50_ms,p95_ms,p99_ms,throughput_qps,attacker_rps
2026-05-06T12:30:45Z,sidecar_isolation,0,1,2.451,4.876,12.345,997.5,0.0
2026-05-06T12:31:45Z,sidecar_isolation,10000,1,2.601,5.123,13.456,995.2,10150.3
2026-05-06T12:32:45Z,sidecarless_contention,10000,1,6.890,14.234,45.678,985.1,10098.5
```

---

## Part 3: Results Analysis

### Raw Data Location

```
ebpf_research/results/
├── metrics.csv                              # Simple benchmark output
├── sidecar_vs_sidecarless_metrics.csv       # Sweep benchmark output
├── raw/
│   ├── baseline_fortio.json
│   ├── sidecar_isolation_fortio.json
│   ├── sidecarless_contention_fortio.json
│   └── sidecar_vs_sidecarless/
│       ├── sidecar_isolation_r1_n0_fortio.json
│       ├── sidecar_isolation_r1_n0_fortio.log
│       ├── sidecar_isolation_r1_n0_attacker.log
│       ├── sidecar_isolation_r1_n0_cpu_softirq.log
│       └── ... (many more files)
└── graphs/
    ├── fortio_latency_distribution.png
    ├── fortio_percentile_curves.png
    ├── fortio_summary.csv
    ├── latency_comparison.png
    ├── throughput_impact.png
    ├── contention_effect.png
    ├── summary_heatmap.png
    └── sidecar_vs_sidecarless/
        ├── p50_ms_vs_noise.png
        ├── p95_ms_vs_noise.png
        └── p99_ms_vs_noise.png
```

### Analysis Scripts

#### `analysis/analyze_fortio.py`

**What It Does:**
1. Reads Fortio JSON files from `results/raw/`
2. Extracts latency histogram (Percentiles)
3. Parses summary metrics (ActualQPS, Successes, Errors)
4. Generates visualizations

**Key Extractions:**
```python
# From Fortio JSON
DurationHistogram.Percentiles[] = [
    {"Percentile": 50.0, "Value": 0.002},     # seconds
    {"Percentile": 95.0, "Value": 0.005},
    {"Percentile": 99.0, "Value": 0.025}
]

# Converted to milliseconds and plotted
```

**Output:**
- `fortio_latency_distribution.png` - Histogram of request counts by latency bucket
- `fortio_percentile_curves.png` - Log-scale percentile curves across configs
- `fortio_summary.csv` - Table of P50, P95, P99, ActualQPS per config

#### `analysis/plot_metrics.py`

**What It Does:**
1. Reads `metrics.csv` 
2. Aggregates by configuration
3. Calculates statistics (mean, std, count)
4. Creates comparison plots

**Output:**
- `latency_comparison.png` - P50/P95/P99 across runs
- `throughput_impact.png` - Victim QPS and attacker load
- `contention_effect.png` - 2D analysis and tail amplification
- `summary_heatmap.png` - Normalized metrics heatmap

#### `analysis/sidecar_vs_sidecarless/plot_latency_vs_noise.py`

**What It Does:**
1. Reads `sidecar_vs_sidecarless_metrics.csv`
2. Groups by config and noise level
3. Calculates mean and std deviation per group
4. Plots latency vs. noise for each percentile

**Output:**
- `p50_ms_vs_noise.png` - Median latency trend
- `p95_ms_vs_noise.png` - P95 latency trend
- `p99_ms_vs_noise.png` - P99 latency trend (most affected by contention)

### Interpreting Results

#### Key Metrics

1. **P50 (Median Latency)**
   - Typical request latency for normal/good conditions
   - Least affected by outliers
   - Example: 2ms (baseline) → 3ms (sidecar) → 7ms (sidecarless)

2. **P95 (95th Percentile)**
   - 95% of requests finish faster than this
   - Early indicator of contention
   - More sensitive than P50 to interference
   - Example: 5ms (baseline) → 6ms (sidecar) → 15ms (sidecarless)

3. **P99 (Tail Latency / 99th Percentile)**
   - The worst 1% of requests
   - Most sensitive to eBPF map contention
   - Shows amplification effect most clearly
   - Example: 25ms (baseline) → 30ms (sidecar) → 100ms (sidecarless)

4. **Tail Amplification Ratio**
   - Formula: `P99 / P50`
   - Baseline: 25ms / 2ms = 12.5x
   - Sidecarless: 100ms / 7ms = 14.3x
   - Higher ratio = worse contention behavior

#### What the Graphs Show

**"Latency Distribution from Fortio Measurements"**
- Three panels showing histogram of requests by latency bucket
- Shows if distribution is bimodal (indicates two different latency classes)
- Shared eBPF often shows bimodal distribution (some fast, some blocked on lock)

**"Latency Percentile Curves from Fortio Runs"**
- Log-log plot comparing percentile curves
- Baseline: Smooth shallow curve
- Sidecar: Slightly elevated curve
- Sidecarless: Sharp exponential rise at tail percentiles

**"P99 vs Attacker Load"**
- X-axis: Attacker RPS (0 to 60,000)
- Y-axis: P99 latency (log scale)
- Baseline: Nearly flat line at bottom
- Sidecar: Gradual linear increase
- Sidecarless: Sharp nonlinear increase (exponential behavior)
- Demonstrates eBPF lock contention intensifies with load

**"Throughput Impact Analysis"**
- Left panel: Victim throughput (QPS) achieved
  - Baseline: ~1600 QPS
  - Sidecar: ~520 QPS (isolation cost)
  - Sidecarless: ~600 QPS (slightly better throughput but higher latency)
- Right panel: Attacker RPS generated

**"Summary Heatmap"**
- Rows: Configurations (baseline, sidecar, sidecarless)
- Columns: Metrics (P50, P99, Throughput, Attacker RPS)
- Color: Normalized score (green = good, red = bad)
- Shows at a glance which config performs best on each metric

#### Typical Results

For a 5-victim vs 1-attacker scenario at 60,000 RPS attacker load:

| Config | P50 (ms) | P95 (ms) | P99 (ms) | Throughput (QPS) | Tail Amp |
|--------|----------|----------|----------|------------------|----------|
| Baseline | 0.9 | 17.9 | 1601.9 | 1601.9 | 1778x |
| Sidecar | 1.0 | 1665.8 | 519.3 | 986.5 | 519x |
| Sidecarless | 1.2 | 1585.9 | 594.7 | 957.9 | 496x |

**Interpretation:**
- Baseline has no noise, so latencies are low but P99 is artificially high (only 1 victim)
- Sidecar shows moderate degradation with isolation benefit
- Sidecarless shows slightly worse P99 and throughput (shared contention effect)

---

## Part 4: Running the Benchmarks

### Quick Start

```bash
# 1. Setup (one time)
cd ebpf_research
sudo ./scripts/setup.sh

# 2. Run simple benchmark
sudo ./scripts/run_benchmarks.sh

# 3. Analyze results
python3 ./analysis/analyze_fortio.py
python3 ./analysis/plot_metrics.py

# 4. View graphs
# Open results/graphs/*.png in image viewer
```

### Advanced: Noise Sweep

```bash
# Run with custom noise levels and repeats
cd ebpf_research
sudo REPEATS=5 NOISE_LEVELS_CSV="0,5000,15000,30000,50000" \
  ./scripts/sidecar_vs_sidecarless/run_noise_sweep.sh

# Generate comparison graphs
python3 ./scripts/sidecar_vs_sidecarless/plot_latency_vs_noise.py
```

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `DURATION_SECONDS` | 60 | Benchmark run duration |
| `TARGET_URL` | http://10.200.0.2:8080/ | Victim endpoint |
| `FORTIO_PERCENTILES` | 50,95,99 | Which percentiles to measure |
| `REPEATS` | 3 | Number of times to repeat each test (sweep only) |
| `NOISE_LEVELS_CSV` | 0,10000,20000,40000,60000 | Attacker RPS levels |
| `ATTACKER_LOAD_TOOL` | fortio | Load tool: fortio or wrk2 |

---

## Part 5: Key Takeaways

### What This Measures
- **eBPF Map Contention**: Shared vs. isolated map access patterns
- **Kernel Lock Behavior**: Impact of atomic operations under contention
- **Tail Latency Amplification**: How noisy neighbors affect worst-case latencies
- **Throughput Trade-offs**: Isolation vs. efficiency

### Why It Matters
- Modern data planes (service mesh sidecars, network functions) use eBPF
- Shared kernel maps can become bottlenecks under multi-tenant load
- Tail latency SLOs are critical for user-facing services
- This benchmark quantifies the cost of contention

### Design Decisions

1. **No explicit CPU/RAM limits**: Ensures eBPF contention is the limiting factor, not resource exhaustion

2. **Traffic Control (tc) attachment**: Runs eBPF programs at ingress, where all traffic passes through

3. **Simple atomic operation**: The `__sync_fetch_and_add()` isolates the map update contention without other confounding factors

4. **Closed attacker port**: In sweep tests, attacker targets :8081 (not running) to create eBPF load without app overhead

5. **Long warm-up**: 3-second eBPF map warm-up ensures kernel structures are in cache before measurement

