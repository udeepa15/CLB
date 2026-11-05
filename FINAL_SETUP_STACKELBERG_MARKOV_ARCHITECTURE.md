# Architecture Specification: DTMC Predictive Stackelberg eBPF QoS Mesh

> **System Component Specification & Formal Documentation**  
> **Core Architecture**: Discrete-Time Markov Chain (DTMC) Predictive Preemptive Stackelberg Leader-Follower Service Mesh  
> **Primary Components**: Linux Traffic Control (TC) Subsystem, Per-CPU eBPF Fast Path, User-Space DTMC Engine, Stackelberg Game-Theoretic Solver

---

## 1. Complete System Component Diagram (Mermaid)

The following component diagram illustrates the closed-loop architecture connecting the multi-tenant data plane, the kernel-level eBPF packet processing pipeline, the stochastic DTMC prediction engine, and the game-theoretic Stackelberg rate controller.

```mermaid
flowchart TB
    %% Styling Classes
    classDef clientLayer fill:#EFF6FF,stroke:#3B82F6,stroke-width:2px,color:#1E3A8A;
    classDef netnsLayer fill:#F3F4F6,stroke:#6B7280,stroke-width:2px,color:#111827;
    classDef kernelData fill:#ECFDF5,stroke:#059669,stroke-width:2px,color:#064E3B;
    classDef bpfMap fill:#FEF3C7,stroke:#D97706,stroke-width:2px,color:#78350F;
    classDef controlEngine fill:#FAF5FF,stroke:#9333EA,stroke-width:2px,color:#581C87;
    classDef gameSolver fill:#FFF1F2,stroke:#E11D48,stroke-width:2px,color:#881337;

    %% 1. Traffic Generation & Client Layer
    subgraph CLIENT_LAYER ["1. Workload & Client Ingress Layer"]
        VIC_CLIENT["Fortio / wrk2 Client<br>(HTTP Benchmark Traffic)"]
        ATT_CLIENT["Attacker / Noisy Neighbor<br>(Multi-rate UDP Flood)"]
    end
    class VIC_CLIENT,ATT_CLIENT clientLayer;

    %% 2. Network Namespace & Host Topology
    subgraph TOPOLOGY_LAYER ["2. Multi-Tenant Network Topology (Host Bridge: br-mesh)"]
        subgraph NS_VICTIM ["Victim Namespaces (ns_victim1..3)"]
            VIC_SRV["Victim Microservice Servers<br>(FastAPI / HTTP Endpoints: 10.0.0.10..12)"]
        end
        subgraph NS_ATTACKER ["Attacker Namespace (ns_attacker)"]
            ATT_SRV["Attacker Endpoint<br>(Noisy Neighbor: 10.0.0.20)"]
        end
    end
    class VIC_SRV,ATT_SRV netnsLayer;

    %% 3. Kernel Space Data Plane
    subgraph KERNEL_SPACE ["3. Kernel Space: eBPF Fast-Path Filter (tc clsact hook)"]
        TC_ENTRY["TC Ingress / Egress Classifier<br>(ebpf_qos_dynamic.c)"]
        IP_PARSE["IP Header Parser & Tenant Demux<br>(saddr match: Leader vs. Follower)"]
        
        TOKEN_BUCKET{"Token Bucket Verification<br>(tokens >= skb->len?)"}
        DROP_ACT["Drop Action: TC_ACT_SHOT<br>(Immediate Kernel Drop)"]
        PASS_ACT["Forward Action: TC_ACT_OK<br>(Deliver to Target Netns)"]

        subgraph BPF_MAPS ["Pinned Per-CPU eBPF Maps (/sys/fs/bpf/tc/globals)"]
            PERCPU_LIMITS[("percpu_rate_limit_map<br>[BPF_MAP_TYPE_PERCPU_HASH]<br>Key: IPv32 | Val: rate_limit_entry")]
            COUNTER_MAP[("update_counter_map<br>[BPF_MAP_TYPE_PERCPU_ARRAY]<br>Cumulative Packet Update Hits")]
            LATENCY_HIST[("lock_latency_hist<br>[BPF_MAP_TYPE_PERCPU_ARRAY]<br>Wait Latency Histogram")]
        end
    end
    class TC_ENTRY,IP_PARSE,TOKEN_BUCKET,DROP_ACT,PASS_ACT kernelData;
    class PERCPU_LIMITS,COUNTER_MAP,LATENCY_HIST bpfMap;

    %% 4. User Space Control Plane
    subgraph CONTROL_PLANE ["4. User Space Control Plane (qos_controller_markov.py - CPU 3)"]
        
        subgraph TELEMETRY_INGEST ["A. Telemetry Ingestion (100ms Polling Tick)"]
            BPF_POLL["bpftool Map Dumper<br>(Read update_counter_map)"]
            HIT_CALC["Contention Metric Calculator<br>Hits/sec H_t = ΔHits / Δt"]
        end

        subgraph DTMC_MODULE ["B. Discrete-Time Markov Chain (DTMC) Engine"]
            DISCRETIZE["Contention Discretization<br>S0: LOW (<5k) | S1: MOD (5k-15k)<br>S2: HIGH (15k-30k) | S3: SEVERE (>=30k)"]
            MATRIX_UPDATE["Online Transition Tracker<br>Counts: N_ij += 1<br>Laplace Smoothing: α = 0.1"]
            PREDICTOR["Spike Predictor<br>P_spike = P(S_t+1 in {HIGH, SEVERE} | S_t)"]
            THRESH_CHECK{"Preemptive Trigger?<br>P_spike >= 0.60 (60%)"}
        end

        subgraph STACKELBERG_SOLVER ["C. Stackelberg Game-Theoretic Optimizer"]
            EFFECTIVE_H["Effective Load Determinator<br>If Preemptive: H_eff = max(H_t, 35000)<br>Else: H_eff = H_t"]
            LEADER_STRAT["Leader Policy (Victim IPs)<br>r_victim = 0 (Uncapped / High Priority)"]
            FOLLOWER_STRAT["Follower Optimal Response (Attacker IP)<br>r*(H_eff) = r_max / [1 + (H_eff / H_0)^γ]<br>Bounds: r_min (500KB/s) <= r* <= r_max (50MB/s)"]
        end

        subgraph MAP_INJECTOR ["D. Kernel Map Updater"]
            BPF_INJECT["bpftool map update<br>(Serialize 64-bit Structs per CPU)"]
            LOG_WRITER["JSONL Telemetry Logger<br>(Record state, P_spike, rate limits)"]
        end
    end
    class BPF_POLL,HIT_CALC,DISCRETIZE,MATRIX_UPDATE,PREDICTOR,THRESH_CHECK controlEngine;
    class EFFECTIVE_H,LEADER_STRAT,FOLLOWER_STRAT,BPF_INJECT,LOG_WRITER gameSolver;

    %% Inter-Component Data Flows
    VIC_CLIENT -->|HTTP Ingress Queries| TC_ENTRY
    ATT_CLIENT -->|UDP Contention Flood| TC_ENTRY

    TC_ENTRY --> IP_PARSE
    IP_PARSE -->|Lookup Source IP| PERCPU_LIMITS

    PERCPU_LIMITS -->|Rate = 0 (Victim Leader)| PASS_ACT
    PERCPU_LIMITS -->|Rate > 0 (Follower)| TOKEN_BUCKET

    TOKEN_BUCKET -->|Tokens Depleted| DROP_ACT
    TOKEN_BUCKET -->|Tokens Available| PASS_ACT

    PASS_ACT -->|Deliver Packets| VIC_SRV
    PASS_ACT -->|Deliver Packets| ATT_SRV

    TC_ENTRY -.->|Increment Hits| COUNTER_MAP
    TC_ENTRY -.->|Record Duration| LATENCY_HIST

    COUNTER_MAP -.->|Async Telemetry Dump| BPF_POLL
    BPF_POLL --> HIT_CALC
    HIT_CALC --> DISCRETIZE
    DISCRETIZE --> MATRIX_UPDATE
    MATRIX_UPDATE --> PREDICTOR
    PREDICTOR --> THRESH_CHECK

    THRESH_CHECK -->|Yes: Preemptive Threat| EFFECTIVE_H
    THRESH_CHECK -->|No: Reactive State| EFFECTIVE_H

    EFFECTIVE_H --> FOLLOWER_STRAT
    EFFECTIVE_H --> LEADER_STRAT

    LEADER_STRAT --> BPF_INJECT
    FOLLOWER_STRAT --> BPF_INJECT

    BPF_INJECT -.->|Write Dynamic Limits r_i| PERCPU_LIMITS
    BPF_INJECT --> LOG_WRITER
```

---

## 2. Core Architectural Subsystems

### 2.1. Kernel-Space Fast-Path Layer (`ebpf_qos_dynamic.c`)
The fast-path executes inside the Linux kernel Traffic Control (`tc`) `clsact` hook on host-facing virtual Ethernet interfaces (`veth-att-br`, `veth-vic*-br`, `br-mesh`).
1. **Zero-Copy Ingress Classification**: Upon packet arrival, the BPF program parses Ethernet and IPv4 headers to extract source IP (`iph->saddr`) and packet length (`skb->len`).
2. **Per-CPU Sharded Storage (`BPF_MAP_TYPE_PERCPU_HASH`)**:
   * Storing tenant rate limits in standard hash maps introduces severe cross-CPU spinlock contention (`htab_lock_bucket`) under multi-core packet arrival.
   * `percpu_rate_limit_map` shards state across CPU cores, eliminating spinlock contention during high-speed lookups.
3. **In-Kernel Token Bucket Enforcement**:
   * **Leader Tenants (Victim IPs: `10.0.0.10..12`)**: Configured with `rate_bytes_per_sec = 0`, bypassing rate limits entirely (`TC_ACT_OK`).
   * **Follower Tenants (Attacker IP: `10.0.0.20`)**: Evaluated against in-memory token balances. If sufficient tokens exist, tokens are decremented and the packet passes (`TC_ACT_OK`). If depleted, the packet is immediately dropped (`TC_ACT_SHOT`) without consuming user-space CPU time.
4. **Kernel Telemetry Instrumentation**: Every packet increments an atomic counter in `update_counter_map` and writes packet processing duration into log2 histogram bins in `lock_latency_hist`.

---

### 2.2. Discrete-Time Markov Chain (DTMC) Engine
The user-space controller polls kernel telemetry every $100\text{ ms}$ ($\Delta t = 0.1\text{ s}$) to observe the global map update rate $H_t$:

$$H_t = \frac{\Delta \text{Hits}}{\Delta t} \quad [\text{hits/sec}]$$

#### A. State Discretization
$H_t$ is mapped into a 4-state Markov space $\mathcal{S} = \{S_0, S_1, S_2, S_3\}$:
* **$S_0$ (`LOW`)**: $H_t < 5,000\text{ hits/sec}$ (Quiescent baseline traffic).
* **$S_1$ (`MODERATE`)**: $5,000 \le H_t < 15,000\text{ hits/sec}$ (Emerging traffic rise).
* **$S_2$ (`HIGH`)**: $15,000 \le H_t < 30,000\text{ hits/sec}$ (Severe lock contention risk).
* **$S_3$ (`SEVERE`)**: $H_t \ge 30,000\text{ hits/sec}$ (Critical kernel saturation).

#### B. Online Transition Probability Estimation
The controller maintains an empirical $4 \times 4$ transition frequency matrix $N_{ij}$ tracking observed transitions $S_t \to S_{t+1}$. Probabilities are updated online using **Laplace smoothing** ($\alpha = 0.1$) to prevent zero-probability lockups during initial cycles:

$$P_{ij} = P(S_{t+1} = S_j \mid S_t = S_i) = \frac{N_{ij} + \alpha}{\sum_{k=0}^3 (N_{ik} + \alpha)}$$

#### C. Preemptive Spike Predictor
The cumulative probability of transitioning to acute contention states ($S_2$ or $S_3$) in the next cycle is evaluated:

$$P_{\text{spike}} = P(S_{t+1} \in \{S_2, S_3\} \mid S_t) = P_{i,2} + P_{i,3}$$

* **Preemptive Activation**: If $P_{\text{spike}} \ge 0.60$ (hyperparameter threshold $60\%$), the controller infers an imminent contention surge.
* **Effective Load Injection**: Instead of waiting for contention to manifest reactively, it assigns:
  $$H_{\text{eff}} = \max(H_t, 35,000\text{ hits/sec})$$
  triggering early rate reduction *before* packet queues build up in the kernel.

---

### 2.3. Game-Theoretic Stackelberg Rate Optimizer
The rate computation models resource allocation as a single-leader, multi-follower hierarchical Stackelberg game:
* **Leader Objective**: Shield victim tenants from tail-latency degradation, providing uncapped capacity ($r_{\text{leader}} = 0$).
* **Follower Objective**: Maximize throughput subject to the leader's penalty constraints. The optimal follower rate $r^*(H_{\text{eff}})$ follows a continuous sigmoid decay function:

$$r^*(H_{\text{eff}}) = \max\left(r_{\min}, \min\left(r_{\max}, \frac{r_{\max}}{1 + \left(\frac{H_{\text{eff}}}{H_0}\right)^\gamma}\right)\right)$$

| Parameter | Value | Description |
| :--- | :---: | :--- |
| **$r_{\max}$** | $50\text{ MB/s}$ ($5 \times 10^7\text{ B/s}$) | Maximum unconstrained bandwidth under calm conditions |
| **$r_{\min}$** | $500\text{ KB/s}$ ($5 \times 10^5\text{ B/s}$) | Starvation floor ensuring connection survivability |
| **$H_0$** | $15,000\text{ hits/sec}$ | Contention inflection knee point identified during profiling |
| **$\gamma$** | $2.0$ | Sigmoid decay sensitivity exponent |

---

### 2.4. Low-Overhead Map Injection
1. The user-space controller formats a 48-byte Little-Endian C struct (`struct rate_limit_entry`) for each CPU core.
2. The struct contains updated `rate_bytes_per_sec` and reset token boundaries.
3. The values are committed to `/sys/fs/bpf/tc/globals/percpu_rate_limit_map` via `bpftool map update pinned` in a single system execution.
4. Decisions and transitions are asynchronously recorded in `qos_controller_markov_log.jsonl`.

---

## 3. Comparison: Reactive Stackelberg vs. DTMC Predictive Stackelberg

| Feature | Reactive Controller (`qos_dynamic`) | DTMC Predictive Controller (`qos_dtmc_predictive`) |
| :--- | :--- | :--- |
| **Trigger Mechanism** | Reacts after contention $H_t$ or $dC/dt$ has already surged | Anticipates contention spikes via $P(S_{t+1} \in \{S_2, S_3\} \mid S_t) \ge 0.60$ |
| **Control Interval** | Dynamic adaptive polling ($20\text{ ms} \dots 1000\text{ ms}$) | Fixed synchronous stochastic evaluation loop ($100\text{ ms}$) |
| **Transient Tail Latency** | Exhibits momentary P99 spikes during sudden step bursts | Preemptively suppresses noisy tenants, keeping P99 flat ($\sim 4.1\text{ ms}$) |
| **Model Adaptation** | Deterministic derivative thresholding | Online continuous learning via Laplace-smoothed Markov transition matrix |
