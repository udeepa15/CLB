# Comprehensive Testbed Setup Specification: Sidecar vs Sidecarless eBPF Performance Benchmarking

> **Purpose**: This document provides an exhaustive, self-contained technical specification of the experimental testbed used for benchmarking **Sidecarless eBPF** vs **Sidecar Proxy** architectures under noisy-neighbor eBPF map lock contention. It is structured for ingestion into LLMs and automated analysis tools.

---

## 1. Executive Summary & Research Context

### Research Objective
The testbed measures **P99 tail latency degradation** and resource overhead in containerized service mesh environments when running under high-rate background traffic ("noisy neighbors"). Specifically, it quantifies kernel spinlock contention on eBPF BPF hash map operations (`htab_lock_bucket`) compared to conventional user-space sidecar reverse proxies (`socat` / Envoy pattern).

### Core Hypothesis
In **Sidecarless eBPF** architectures using shared eBPF BPF hash maps (`BPF_MAP_TYPE_HASH`), every packet traversing an interface executes BPF lookup and update operations (`bpf_map_lookup_elem` / `bpf_map_update_elem`). When multiple CPU cores process incoming/outgoing packets concurrently, accessing shared global keys forces all CPU cores to serialize on the identical internal kernel **`htab_lock_bucket` spinlock**, inducing severe P99 tail latency spikes proportional to packet arrival rates and CPU core counts.

---

## 2. Network Data Plane & Topology Architecture

The testbed supports two topological setups: **Single-Node Multi-Victim Topology** and **10-Node Cluster Topology**.

```
                           +-----------------------------------+
                           |        Linux Bridge: br-mesh      |
                           |            (10.0.0.1/24)          |
                           +-----------------+-----------------+
                                             |
           +---------------------------------+---------------------------------+
           |                                 |                                 |
+----------+----------+           +----------+----------+           +----------+----------+
| Attacker Namespace  |           | Victim 1 Namespace  |           | Victim 2 Namespace  |
|   (ns_attacker)     |           |   (ns_victim1)      |           |   (ns_victim2)      |
|    10.0.0.20/24     |           |    10.0.0.10/24     |           |    10.0.0.11/24     |
| (hping3 / wrk2)     |           | (victim_server)   |           | (victim_server)   |
+---------------------+           +---------------------+           +---------------------+
```

### Topology A: 3-Victim Single-Node Architecture ([setup_topology.sh](file:///home/udeepa/CLB/test_bed/setup_topology.sh))
- **Host Bridge**: `br-mesh` with IP `10.0.0.1/24`.
- **Attacker Pod Namespace**: `ns_attacker` with IP `10.0.0.20/24`, connected via veth pair `veth-attacker` $\leftrightarrow$ `veth-att-br`.
- **Victim Pod Namespaces**:
  - `ns_victim1`: IP `10.0.0.10/24`, veth pair `veth-victim1` $\leftrightarrow$ `veth-vic1-br`.
  - `ns_victim2`: IP `10.0.0.11/24`, veth pair `veth-victim2` $\leftrightarrow$ `veth-vic2-br`.
  - `ns_victim3`: IP `10.0.0.12/24`, veth pair `veth-victim3` $\leftrightarrow$ `veth-vic3-br`.
- **IP Forwarding**: Host IPv4 routing enabled (`sysctl net.ipv4.ip_forward=1`). Default gateway in all namespaces set to `10.0.0.1`.

### Topology B: 10-Node Cluster Topology ([setup_cluster_10nodes.sh](file:///home/udeepa/CLB/test_bed/setup_cluster_10nodes.sh))
- **10 Independent Victim Namespaces**: `ns_victim1` through `ns_victim10` assigned IPs `10.0.0.10` through `10.0.0.19`.
- **Noisy Node Contention Sweep**: Evaluates 0 to 5 active noisy attacker nodes flooding traffic across the shared bridge.

---

## 3. Container Runtime & Workload Isolation

Containers are executed directly using **OCI `runc`** without requiring Docker/containerd daemons to guarantee low-overhead, deterministic process scheduling.

### Image RootFS & Build Setup ([build_runc_bundles.sh](file:///home/udeepa/CLB/test_bed/build_runc_bundles.sh))
- **Base Rootfs**: Minimal Alpine Linux (`alpine-minirootfs-3.18.4-x86_64`).
- **Victim Bundle (`victim_bundle`)**:
  - Pre-installed packages: `python3`, `fortio` binary (`/usr/bin/fortio` copied from host).
  - OCI Spec configuration: `config.json` configured with `terminal: false`, `user: {uid: 0, gid: 0}`.
  - Capabilities: `CAP_NET_BIND_SERVICE`, `CAP_KILL`, `CAP_AUDIT_WRITE`.
  - Network Namespace: Joined directly to `/var/run/netns/ns_victim<ID>`.
- **Attacker Bundle (`attacker_bundle`)**:
  - Pre-installed packages: `build-base`, `git`, `wrk2`, `hping3`.
  - Network Namespace: Joined directly to `/var/run/netns/ns_attacker`.

### Multi-Protocol Victim Server ([victim_server.py](file:///home/udeepa/CLB/test_bed/victim_server.py))
A dedicated Python server script executing inside each victim container, supporting four protocol modes:
1. **HTTP Mode (Port 8080)**: Native Python `http.server.BaseHTTPRequestHandler` returning `200 OK` with body `OK` without disk I/O lookups, explicitly bound to `0.0.0.0`.
2. **TCP Echo Mode (Port 8078)**: Multi-threaded TCP socket echo server (`socketserver.ThreadingMixIn`, `socketserver.TCPServer`).
3. **UDP Echo Mode (Port 8078)**: Datagram socket server echoing received UDP packets directly (`sock.recvfrom` $\rightarrow$ `sock.sendto`).
4. **gRPC Mode (Port 8079)**: Native Fortio gRPC ping server (`fortio server -grpc-port 8079 -http-port disabled -tcp-port disabled -udp-port disabled`).

---

## 4. Architectural Modes Under Test

```
                              [ Incoming Packet ]
                                       |
                   +-------------------+-------------------+
                   |                                       |
     +-------------v-------------+           +-------------v-------------+
     |   Sidecarless eBPF Mode   |           |    Sidecar Proxy Mode     |
     +-------------+-------------+           +-------------+-------------+
     | TC ingress/egress qdisc   |           | TC filters DETACHED       |
     | Hooked: ebpf_mesh_router  |           | iptables PREROUTING NAT   |
     | bpf_map_update_elem loop  |           | Redirect --to-ports 8080  |
     | Key == 0 (Shared global)  |           | socat reverse proxy process|
     | Contends on htab_lock     |           | User-space socket copy    |
     +-------------+-------------+           +-------------+-------------+
                   |                                       |
                   +-------------------+-------------------+
                                       |
                            [ Target Application ]
```

### Mode 1: Sidecarless eBPF Architecture ([ebpf_mesh_router.c](file:///home/udeepa/CLB/test_bed/ebpf_mesh_router.c))
- **Hook Attachment**: Loaded via `tc filter add dev <iface> ingress/egress bpf da obj ebpf_mesh_router.o sec classifier`. Attached across all bridge veth interfaces (`veth-att-br`, `br-mesh`, `veth-vic*-br`).
- **Classifier Logic**:
  - Filters for IPv4 packets (`eth->h_proto == ETH_P_IP`).
  - Accesses a globally pinned BPF hash map (`flow_map`, `BPF_MAP_TYPE_HASH`, max 65536 entries).
  - **Shared Lock Contention Key**: Uses a static global key `shared_global_key = 0` for **all** packet lookups and updates regardless of 5-tuple flow parameters.
  - **Contention Iteration Loop**: Executes a `#pragma unroll` loop performing **50 consecutive `bpf_map_update_elem` operations** on `shared_global_key = 0` per packet.
  - **Result**: Forces all competing CPU cores executing SoftIRQs or packet processing to serialize on the exact same kernel `htab_lock_bucket` spinlock.

### Mode 2: Sidecar Proxy Architecture (Reference Baseline)
- **eBPF Detachment**: All eBPF TC qdiscs are detached (`tc qdisc del dev <iface> clsact`), and pinned maps are unlinked.
- **Port Redirection**: `iptables` NAT PREROUTING rule inside victim namespace redirects incoming traffic:
  `ip netns exec ns_victim1 iptables -t nat -A PREROUTING -p tcp --dport <port> -j REDIRECT --to-ports 8080`
- **Reverse Proxy Process**: `socat` process running inside the victim network namespace:
  `nsenter --net=/var/run/netns/ns_victim1 socat TCP-LISTEN:8080,fork,reuseaddr TCP:127.0.0.1:<port>`
- **Result**: Traffic incurs standard user-space context switching and socket copy overhead, but remains completely free of BPF map spinlock contention.

---

## 5. Benchmark Load Parameters & Contention Matrix

Shared load configurations are managed centrally in [config.sh](file:///home/udeepa/CLB/test_bed/config.sh) to prevent configuration drift between experimental runs.

### Load & Target Parameters
- **Fortio Load Generator**: Pinned to CPU core 0 (`taskset -c 0 fortio load ...`).
- **Request Rate (QPS)**: `50 QPS`.
- **Concurrent Connections**: `2 connections`.
- **Duration**: `10 seconds` per trial run.
- **Warmup Duration**: `2 seconds`.
- **Protocols Supported**: `HTTP` (port 8080), `TCP` (port 8078), `UDP` (port 8078), `gRPC` (port 8079).

### Attacker Flood Contention Matrix
Flood traffic is generated from `ns_attacker` using `hping3` running across CPU cores 4–7 (2 parallel workers per core) targeting `10.0.0.10`:

| Flood Level Code | Inter-Packet Delay | Approximate Attacker Packet Rate | Description |
| :--- | :--- | :--- | :--- |
| **`0`** | N/A | **0 pps** | Baseline (No background flood) |
| **`u200`** | 200 $\mu$s | **~5,000 pps (5k pps)** | Low flood intensity |
| **`u20`** | 20 $\mu$s | **~50,000 pps (50k pps)** | Medium flood intensity |
| **`u2`** | 2 $\mu$s | **~500,000 pps (500k pps)** | High flood intensity |
| **`u1`** | 1 $\mu$s | **~1,000,000 pps (1M pps)** | Extreme flood intensity |
| **`flood`** | 0 (Unthrottled) | **Max NIC / Driver Rate** | Raw unthrottled UDP flood |

### Trial Execution & Randomization Controls ([run_multi_run_matrix.py](file:///home/udeepa/CLB/test_bed/run_multi_run_matrix.py))
- **Repetitions**: $N=5$ (or $N=10$) repetitions per cell.
- **Trial Randomization**: All combinations of `(arch, protocol, flood_level)` are shuffled using a fixed random seed (`SEED = 42`).
- **Manifest Pre-Generation**: A full execution plan is pre-written to `manifest.json` before execution begins.
- **Socket Readiness Polling**: Prior to launching Fortio, active socket readiness checks (`wait_for_port()` for TCP/HTTP/gRPC, `wait_for_udp()` for UDP) poll target IP:port with a 15-second retry timeout.
- **Fail-Fast Enforcement**: If container startup fails or Fortio returns connection errors, the script aborts immediately with a `CRITICAL ERROR` to prevent saving partial/corrupted data.

---

## 6. Instrumentation Streams & Metric Collection

Five concurrent instrumentation collectors capture data during every trial run:

```
+-----------------------------------------------------------------------------------+
|                            INSTRUMENTATION STREAMS                                |
+-----------------------+-----------------------+-----------------------------------+
| Data Stream           | Collector / Tool      | Metrics / Format                  |
+-----------------------+-----------------------+-----------------------------------+
| Latency Percentiles   | Fortio Client         | P50, P75, P90, P99, P999 (JSON)   |
| eBPF Ktime Histogram  | collect_ebpf_stats.py | lock_latency_hist (JSONL)         |
| BPF Update Hit Rate   | collect_ebpf_stats.py | update_counter_map (hits/sec)     |
| Container CPU Stats   | collect_cgroup_stats  | /sys/fs/cgroup/.../cpu.stat (CSV) |
| Interface Packet Drops| collect_network_stats | ip -s link / rx_dropped (TXT)     |
| Spinlock Tracing      | bpftrace              | htab_lock_bucket wait kprobe (BT) |
+-----------------------+-----------------------+-----------------------------------+
```

### 1. Fortio Latency Metrics (`fortio_*.json`)
Captures exact per-request round-trip time (RTT) histograms and exports:
- `P50_ms`, `P70_ms`, `P90_ms`, `P99_ms`, `P999_ms`
- `ActualQPS`, `NumThreads`, HTTP/TCP/UDP/gRPC `RetCodes`

### 2. eBPF BPF Maps (`collect_ebpf_stats.py`)
Polls pinned BPF maps at `/sys/fs/bpf/tc/globals/` every 1.0 second:
- **`lock_latency_hist`**: `BPF_MAP_TYPE_PERCPU_ARRAY` (64 slots). Captures microsecond/nanosecond duration from before `bpf_map_update_elem` loop to after, log2-scaled.
- **`update_counter_map`**: `BPF_MAP_TYPE_PERCPU_ARRAY` (1 slot). Sums values across all CPU cores and computes per-second interval diffs (`hits_per_sec`), providing an independent hit-rate measurement.

### 3. Container Cgroup CPU Metrics (`collect_cgroup_stats.py`)
Polls container `/sys/fs/cgroup/victim_container_1/cpu.stat` every 100ms:
- `nr_periods`, `nr_throttled`, `throttled_time`

### 4. Interface Drop Counters (`collect_network_stats.sh`)
Logs interface drop counters from `ip -s -s link show veth-vic1-br`, `/proc/softirqs`, `nstat`, and `ethtool -S`:
- `rx_dropped`, `tx_dropped`, `rx_errors`, `NET_RX` SoftIRQ counters.

### 5. Kernel Spinlock Tracing (`collect_bpftrace_lock.bt`)
Uses `bpftrace` kprobe/kretprobe on kernel function `htab_lock_bucket` to measure raw kernel spinlock acquire wait times in nanoseconds.

---

## 7. Key Empirical Findings & Protocol Comparison

Summary of aggregated results from 5-repetition matrix runs ([plot_multi_run_matrix.py](file:///home/udeepa/CLB/test_bed/plot_multi_run_matrix.py)):

```
HTTP P99 Latency:
Sidecarless eBPF:  5.7 ms (Baseline) ------> 10.7 ms (5k pps) ------> 12.6 ms (Max Flood) [+121% Increase]
Sidecar Proxy:     4.6 ms (Baseline) ------>  4.4 ms (5k pps) ------>  4.4 ms (Max Flood) [FLAT / Immune]

TCP P99 Latency:
Sidecarless eBPF:  1.4 ms (Baseline) ------>  2.1 ms (5k pps) ------>  2.2 ms (Max Flood) [+57% Increase]
Sidecar Proxy:     1.0 ms (Baseline) ------>  1.1 ms (5k pps) ------>  1.1 ms (Max Flood) [FLAT]

gRPC P99 Latency:
Sidecarless eBPF:  2.4 ms (Baseline) ------>  3.2 ms (5k pps) ------>  2.9 ms (Max Flood) [+21% Increase]
Sidecar Proxy:     3.1 ms (Baseline) ------>  2.9 ms (5k pps) ------>  3.3 ms (Max Flood)
```

### Architectural Takeaways
1. **Multi-Packet Protocol Sensitivity**: HTTP exhibits the highest P99 latency growth under eBPF lock contention (+121% latency increase, jumping from 5.7ms to 12.6ms) because stateful multi-packet exchanges (TCP handshake, GET request, HTTP header, response body, FIN) compound per-packet spinlock wait times.
2. **Sidecar Proxy Immunity to BPF Lock Contention**: Sidecar Proxy P99 latency remains flat across flood levels (~4.4ms for HTTP, ~1.1ms for TCP) because sidecar mode detaches TC eBPF classifiers, insulating proxy traffic from kernel BPF hash bucket spinlocks.
3. **Driver Drop Truncation Effect**: Under raw unthrottled `--flood`, NIC ring buffer drop backpressure (`rx_dropped`) discards excess flood packets at the driver layer before eBPF TC classification, stabilizing BPF map update rates and capping P99 tail latency growth.

---

## 8. Directory & File Reference Map

```
/home/udeepa/CLB/test_bed/
├── config.sh                   # Shared load parameters (50 QPS, 2 conns, ports 8080/8079/8078)
├── setup_topology.sh           # Data plane setup script (br-mesh, veth pairs, netns)
├── setup_cluster_10nodes.sh    # 10-node cluster topology setup script
├── ebpf_mesh_router.c          # C source for TC eBPF classifier with shared_global_key = 0 contention
├── attach_ebpf.sh              # Compiles and attaches eBPF classifier to veth qdiscs
├── build_runc_bundles.sh       # Builds Alpine OCI runc bundles for victim and attacker
├── victim_server.py            # Multi-protocol server (HTTP/8080, TCP/8078, UDP/8078, gRPC/8079)
├── collect_ebpf_stats.py       # eBPF map poller (lock_latency_hist & update_counter_map hits/sec)
├── collect_cgroup_stats.py     # Cgroup CPU throttling poller (cpu.stat)
├── collect_network_stats.sh    # Interface rx_dropped / tx_dropped drop counter logger
├── collect_bpftrace_lock.bt    # Kernel htab_lock_bucket spinlock kprobe tracing script
├── run_multi_run_matrix.py     # Main N=5 randomized matrix benchmark runner
├── plot_multi_run_matrix.py    # Plotting script for averaged P99 latency comparison charts
└── plots/
    └── p99_multi_run_avg.png   # Output P99 tail latency comparison graph
```
