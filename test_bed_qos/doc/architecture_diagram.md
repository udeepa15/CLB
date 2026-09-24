# Testbed QoS System Architecture Specification & Diagram

> **Document Goal**: This document provides a complete technical specification and Mermaid architecture diagram of the 4-Architecture QoS Testbed (`~/CLB/test_bed_qos/`).

---

## 1. System Architecture Diagram

```mermaid
graph TB
    subgraph LG ["1. Workload & Load Generation Layer"]
        fortio["Fortio Load Generator (Victim Load: 50 QPS, 2 Conns, Core 0)"]
        hping3["hping3 / Adaptive Attacker (UDP Flood / Bursty / Ramping / Adaptive)"]
    end

    subgraph DP ["2. Data Plane & Container Network Namespaces"]
        subgraph NS_ATT ["Attacker Namespace (ns_attacker)"]
            att_ip["Attacker Pod Container (10.0.0.20/24)"]
        end

        subgraph BRIDGE ["Host Bridge (br-mesh — 10.0.0.1/24)"]
            veth_att["veth-att-br"]
            veth_vic1["veth-vic1-br"]
            veth_vic2["veth-vic2-br"]
            veth_vic3["veth-vic3-br"]
        end

        subgraph NS_VIC1 ["Victim 1 Namespace (ns_victim1)"]
            vic1["Victim Pod 1 (Core 1, 10.0.0.10/24 - HTTP/gRPC/TCP)"]
            socat1["socat Reverse Proxy (Sidecar Arm Only: Port 8080)"]
        end

        subgraph NS_VIC2 ["Victim 2 Namespace (ns_victim2)"]
            vic2["Victim Pod 2 (Core 2, 10.0.0.11/24 - HTTP/gRPC/TCP)"]
        end

        subgraph NS_VIC3 ["Victim 3 Namespace (ns_victim3)"]
            vic3["Victim Pod 3 (Core 3, 10.0.0.12/24 - HTTP/gRPC/TCP)"]
        end
    end

    subgraph EBPF ["3. TC eBPF Subsystem & Architectural Arms (tc clsact ingress/egress)"]
        subgraph ARMS ["Evaluated Architectural Arms"]
            arm_sidecarless["sidecarless: Global Key Hash Map (key=0), htab_lock_bucket spinlock contention"]
            arm_sidecar["sidecar: iptables NAT PREROUTING + socat user-space reverse proxy"]
            arm_tiered["qos_tiered: Alibaba Terway 3-Tier Priority BPF_MAP_TYPE_HASH (src IP key)"]
            arm_dynamic["qos_dynamic: Beeswax Per-CPU Fast-Path Token Bucket BPF_MAP_TYPE_PERCPU_HASH"]
        end
    end

    subgraph BPFFS ["4. Pinned BPF Filesystem Maps (/sys/fs/bpf/tc/globals/)"]
        counter_map["update_counter_map (Per-CPU Map Update Hits)"]
        latency_map["lock_latency_hist (Spinlock Wait Latency Histogram)"]
        percpu_map["percpu_rate_limit_map (Dynamic Tenant Token Bucket Limits)"]
        tenant_map["tenant_qos_map (Static Priority Tiering Limits)"]
    end

    subgraph CTRL ["5. Telemetry & Control Plane (Userspace)"]
        controller["qos_controller.py (Stackelberg Game Equilibrium Solver)"]
        collectors["Telemetry Collectors (collect_ebpf_stats.py, collect_cgroup_stats.py, collect_bpftrace_lock.bt)"]
        runner["Benchmark Matrix Runner & Plotter (run_multi_run_matrix.py, plot_multi_run_matrix.py)"]
    end

    %% Data Plane Traffic Connections
    hping3 -->|Injects Attacker Traffic| att_ip
    fortio -->|Generates Victim Requests| BRIDGE

    att_ip <-->|veth-attacker| veth_att
    veth_vic1 <-->|veth-victim1| vic1
    veth_vic2 <-->|veth-victim2| vic2
    veth_vic3 <-->|veth-victim3| vic3

    veth_att <--> EBPF
    veth_vic1 <--> EBPF
    veth_vic2 <--> EBPF
    veth_vic3 <--> EBPF

    %% eBPF & BPF Map Interactions
    arm_sidecarless -.->|Serializes Updates on Key 0| counter_map
    arm_tiered -.->|Reads Tier & Deducts Tokens| tenant_map
    arm_dynamic -.->|Fast-Path Per-CPU Token Check| percpu_map

    EBPF -.->|Logs Update Hits| counter_map
    EBPF -.->|Logs Latency Histograms| latency_map

    %% Userspace Control Loop & Telemetry
    counter_map -->|Polls Hits/sec via bpftool| controller
    latency_map -->|Reads Contention Signals| collectors
    controller -->|Writes Dynamic Rate Caps| percpu_map

    collectors -->|Telemetry Logs| runner
    fortio -->|Fortio JSON Tail Latency Results| runner
```

---

## 2. Layer-by-Layer Architectural Breakdown

### Layer 1: Workload & Load Generation Layer
* **Fortio Load Generator**: Runs on CPU Core 0 using `fortio load`. Generates uniform benchmark traffic (50 QPS, 2 connections) targeting the Victim Pod IPs (`10.0.0.10`..`12`).
* **Attacker Load Generator**: Runs in `ns_attacker` (or pinned to background host cores 4–7) using `hping3` or `AdaptiveAttackerThread`. Generates UDP flood patterns (steady-state, bursty, ramping, or rational adaptive polling).

### Layer 2: Data Plane & Container Network Namespaces
* **Attacker Pod Namespace (`ns_attacker`)**: Isolated netns assigned IP `10.0.0.20/24`. Connects to host bridge `br-mesh` via `veth-attacker` <-> `veth-att-br`.
* **Victim Pod Namespaces (`ns_victim1`..`3`)**: Isolated netns assigned IPs `10.0.0.10/24`, `10.0.0.11/24`, `10.0.0.12/24`. Each container runs an OCI `runc` instance executing `victim_server.py` pinned to dedicated CPU cores (Cores 1, 2, 3).
* **Host Bridge (`br-mesh`)**: Linux bridge (`10.0.0.1/24`) connecting all container veth pairs.

### Layer 3: TC eBPF Subsystem & Evaluated Architectural Arms
Attached to the Traffic Control (`tc`) `clsact` hook on host veth interfaces:
1. **`sidecarless`**: Baseline eBPF router (`ebpf_mesh_router.c`) performing 50 map updates per packet targeting `shared_global_key = 0`. Forces cross-CPU spinlock contention on `htab_lock_bucket`.
2. **`sidecar`**: User-space proxy reference. TC eBPF filters are detached; `iptables` NAT PREROUTING redirects traffic to `socat` reverse proxy listening on port 8080 inside the victim netns.
3. **`qos_tiered`**: Alibaba Terway-style 3-tier static priority classifier (`ebpf_qos_tiered.c`). Uses `BPF_MAP_TYPE_HASH` keyed per source IP (`__u32 src_ip`). `TIER_L0` (victims) is uncapped, while `TIER_L1`/`TIER_L2` (attacker) is rate-capped via token buckets.
4. **`qos_dynamic`**: Fast-path dynamic rate limiter (`ebpf_qos_dynamic.c`). Uses `BPF_MAP_TYPE_PERCPU_HASH` sharded per CPU (Beeswax SIGCOMM '26 design) to eliminate cross-CPU lock stalls.

### Layer 4: Pinned BPF Filesystem Maps (`/sys/fs/bpf/tc/globals/`)
* **`update_counter_map`**: `BPF_MAP_TYPE_PERCPU_ARRAY` tracking total eBPF map update operations per second.
* **`lock_latency_hist`**: `BPF_MAP_TYPE_PERCPU_ARRAY` recording lock wait latency in log2 nanosecond buckets.
* **`percpu_rate_limit_map`**: `BPF_MAP_TYPE_PERCPU_HASH` storing per-tenant dynamic token-bucket rate limits and burst capacities.
* **`tenant_qos_map`**: `BPF_MAP_TYPE_HASH` storing static tier classifications and token bucket states for `qos_tiered`.

### Layer 5: Telemetry & Control Plane (Userspace)
* **Stackelberg QoS Controller (`qos_controller.py`)**: Runs a 200ms control loop polling `update_counter_map` via `bpftool`. Calculates the follower (attacker) Stackelberg rate cap $r^*(H) = \frac{r_{\max}}{1 + (H / H_{\text{threshold}})^\gamma}$ and updates `percpu_rate_limit_map`.
* **Telemetry Collectors**: `collect_ebpf_stats.py`, `collect_cgroup_stats.py`, and `collect_bpftrace_lock.bt` collect real-time latency histograms, CPU throttling, and kernel lock wait stats.
* **Matrix Runner & Plotter**: `run_multi_run_matrix.py` coordinates multi-repetition benchmark runs across all architectures and flood levels, outputting raw JSON/CSV data for `plot_multi_run_matrix.py`.

---

## 3. Data Flow & Execution Sequence

```mermaid
sequenceDiagram
    autonumber
    participant Fortio as Fortio Load Generator
    participant Attacker as Attacker (hping3)
    participant TC as eBPF TC Classifier (clsact)
    participant Maps as Pinned BPF Maps
    participant Ctrl as qos_controller.py
    participant Victim as Victim Pod (victim_server)

    Attacker->>TC: Send UDP Flood / Packets
    Fortio->>TC: Send HTTP / gRPC / TCP Requests

    alt sidecarless Arm
        TC->>Maps: Update Global Key 0 (Spinlock Contention)
    else qos_tiered Arm
        TC->>Maps: Lookup src_ip in tenant_qos_map
        alt Tier L0 (Victim)
            TC->>Victim: Forward Immediately (TC_ACT_OK)
        else Tier L2 (Attacker)
            TC->>TC: Check Token Bucket -> Drop if exceeded (TC_ACT_SHOT)
        end
    else qos_dynamic Arm
        TC->>Maps: Fast-path per-CPU lookup in percpu_rate_limit_map
        alt Uncapped / Tokens Available
            TC->>Victim: Forward Packet (TC_ACT_OK)
        else Rate Limit Exceeded
            TC--xTC: Fast-Path Drop (TC_ACT_SHOT)
        end
    end

    loop Every 200ms (qos_dynamic Control Loop)
        Ctrl->>Maps: Read update_counter_map (Hits/sec)
        Ctrl->>Ctrl: Solve Stackelberg Leader-Follower Equilibrium Rate
        Ctrl->>Maps: bpftool map update percpu_rate_limit_map
    end

    Victim-->>Fortio: Return Response (Recorded in P50/P90/P99 Fortio JSON)
```
