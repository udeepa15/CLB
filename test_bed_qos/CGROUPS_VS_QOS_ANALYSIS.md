# Comparative Evaluation: Linux Cgroups vs. eBPF QoS Architectures

> **Document Purpose**: Comprehensive technical analysis of benchmark evaluations comparing traditional Linux Control Groups (Cgroups v2 CFS CPU Quota) against eBPF-native Quality of Service (QoS) architectures in a sidecarless service mesh.  
> **Source Testbed**: `~/CLB/test_bed_qos/markov_experiments/test_cgroups_vs_solution.py`  
> **Telemetry Source**: `~/CLB/test_bed_qos/markov_experiments/results/cgroups_vs_solution/` & `~/CLB/test_bed/results/cgroups/20260721_213813/`  
> **Associated Generator**: `~/CLB/test_bed_qos/markov_experiments/plot_cgroups_vs_solution_separate.py`

---

## 1. Executive Summary & Testbed Setup

In containerized cloud environments, operators routinely rely on **Linux Control Groups (Cgroups v2)** to isolate multi-tenant workloads by enforcing CPU bandwidth limits via the Completely Fair Scheduler (CFS quotas: `cpu.max` / `cpu.cfs_quota_us`). However, modern sidecarless service meshes (e.g., Ambient mesh, Cilium, Terway) execute packet routing, connection tracking, and protocol filtering directly within the Linux kernel via **eBPF programs** attached to Traffic Control (`tc`) and XDP hooks.

This benchmark evaluates a fundamental question:  
**Can traditional operating system CPU throttling (Cgroups) mitigate noisy-neighbor interference in a sidecarless eBPF mesh, or is data-plane ingress QoS strictly required?**

### Experimental Configuration
* **Victim 1 (`10.0.0.10:8080`)**: HTTP microservice acting as the direct target of aggressive background traffic.
* **Victim 2 (`10.0.0.11:8080`)**: Innocent bystander HTTP microservice sharing the node and kernel eBPF state, probed by `fortio` at 50 QPS.
* **Attacker (`10.0.0.20`)**: Multi-rate high-frequency UDP flooder (`u20` intensity: 50,000 pps) targeting Victim 1.
* **Evaluated Arms**:
  1. **Sidecarless Baseline**: Unconstrained sidecarless eBPF mesh (no QoS, 100% quota).
  2. **Cgroups 80% / 50% Quota**: Standard sidecarless eBPF mesh with container CPU capped at 80% and 50% of 1 CPU core via Cgroups v2 (`period=100,000 µs`, `quota=80,000 / 50,000 µs`).
  3. **QoS Tiered**: Static eBPF priority classification (Terway L0/L1 model) enforcing strict packet drop/pass at the ingress interface.
  4. **QoS Dynamic**: User-space game-theoretic Stackelberg rate controller adjusting per-tenant token buckets based on real-time contention.
  5. **QoS Markov (DTMC Predictive)**: Discrete-Time Markov Chain predictive controller anticipating contention spikes and preemptively throttling noisy neighbors.

---

## 2. Graph 1: Head-to-Head Tail Latency Comparison

![Graph 1: Head-to-Head Tail Latency Comparison](file:///home/udeepa/CLB/test_bed/plots/cgroups_vs_qos_p99_head_to_head.png)

* **Plot Location**: [`/home/udeepa/CLB/test_bed/plots/cgroups_vs_qos_p99_head_to_head.png`](file:///home/udeepa/CLB/test_bed/plots/cgroups_vs_qos_p99_head_to_head.png)

### Benchmark Data (Victim Bystander P99 under `u20` Contention)

| Architecture / Policy | Category | P99 Tail Latency | Latency vs. Baseline | Isolation Effectiveness |
| :--- | :--- | :---: | :---: | :--- |
| **Sidecarless Baseline** | Unconstrained Baseline | **$3.98\text{ ms}$** | Baseline ($1.00\times$) | None (Kernel lock contention unhindered) |
| **Cgroups 50% Quota** | OS CFS Quota | **$2.93\text{ ms}$** | $26\%$ reduction | Modest (Throttles user space, ignores kernel) |
| **Cgroups 80% Quota** | OS CFS Quota | **$2.99\text{ ms}$** | $25\%$ reduction | Modest (Throttles user space, ignores kernel) |
| **QoS Tiered (Static eBPF)** | Proposed eBPF QoS | **$1.98\text{ ms}$** | **$50\%$ reduction** | **Complete** (Ingress drop before lock acquisition) |
| **QoS Dynamic (Stackelberg)** | Proposed eBPF QoS | **$1.99\text{ ms}$** | **$50\%$ reduction** | **Complete** (Optimal game-theoretic rate throttling) |
| **QoS Markov (DTMC Predictive)**| Proposed eBPF QoS | **$1.95\text{ ms}$** | **$51\%$ reduction** | **Complete** (Preemptive stochastic rate capping) |

### Technical Reasons & Mechanistic Breakdown

1. **The Inherent Failure of Cgroups in the Kernel Data Plane**:
   * Cgroups regulate user-space task scheduling by restricting the execution time granted to container threads on the CFS runqueue.
   * However, eBPF programs attached to the Traffic Control (`tc`) hook execute within **SoftIRQ / NAPI bottom-half context** inside the Linux kernel *before* packets reach user space.
   * When the attacker transmits a 50,000 pps UDP flood, kernel SoftIRQ workers process incoming packets and acquire shared BPF hash map bucket spinlocks (`htab_lock_bucket`).
   * Because SoftIRQ processing is charged to the kernel rather than the container's CFS quota, Cgroups cannot prevent the high-frequency spinlock collisions. Cgroups only slightly dampen tail latency ($3.98\text{ ms} \to 2.93\text{--}2.99\text{ ms}$) by limiting the user-space process from executing extra tasks, leaving the core kernel contention unmitigated.
2. **Why Proposed eBPF QoS Solutions Succeed**:
   * All three eBPF QoS solutions operate directly at the kernel ingress boundary.
   * By implementing per-CPU sharded maps (`BPF_MAP_TYPE_PERCPU_HASH`), cross-core spinlock contention is eliminated at the architectural level.
   * High-priority bystander flows bypass throttling, while attacker flows are dropped or rate-limited via in-kernel token buckets before lock acquisition, bounding P99 latency flat at $\sim 1.95\text{--}1.98\text{ ms}$.

---

## 3. Graph 2: CPU Quota Sensitivity Analysis vs. eBPF QoS Floors

![Graph 2: CPU Quota Sensitivity Analysis](file:///home/udeepa/CLB/test_bed/plots/cgroups_sweep_vs_qos_thresholds.png)

* **Plot Location**: [`/home/udeepa/CLB/test_bed/plots/cgroups_sweep_vs_qos_thresholds.png`](file:///home/udeepa/CLB/test_bed/plots/cgroups_sweep_vs_qos_thresholds.png)

### Layout Architecture: 2-Panel Macro/Micro Representation
To eliminate visual collision between tightly clustered QoS floors around $1.95\text{--}1.99\text{ ms}$, Graph 2 is rearranged into a dual-panel arrangement:
* **Panel A (Macro Contention Overview, $0\text{--}18.5\text{ ms}$)**: Contrasts the severe non-linear fluctuations of Cgroups CFS Quotas against the shaded sub-2ms eBPF QoS performance band.
* **Panel B (High-Resolution View, $1.92\text{--}2.02\text{ ms}$)**: Magnifies the sub-2ms region, displaying distinct data points and marker styles across all container quota percentages ($80\%, 85\%, 90\%, 95\%, 99\%$) to cleanly differentiate **QoS Dynamic ($1.989\text{ ms}$)**, **QoS Tiered ($1.984\text{ ms}$)**, and **QoS Markov ($1.952\text{ ms}$)**.

### Benchmark Data (CFS Quota Sweep vs. Separated QoS Data Points)

| Container CPU Quota (`cpu.max`) | Cgroups P50 (Median) | Cgroups P99 (Tail) | QoS Dynamic (Stackelberg) | QoS Tiered (Static eBPF) | QoS Markov (DTMC Predictive) |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **80% Quota** | $3.3\text{ ms}$ | **$10.0\text{ ms}$** | $1.989\text{ ms}$ | $1.984\text{ ms}$ | **$1.952\text{ ms}$** |
| **85% Quota** | $2.4\text{ ms}$ | **$6.0\text{ ms}$** | $1.989\text{ ms}$ | $1.984\text{ ms}$ | **$1.952\text{ ms}$** |
| **90% Quota** | $3.4\text{ ms}$ | **$14.0\text{ ms}$** | $1.989\text{ ms}$ | $1.984\text{ ms}$ | **$1.952\text{ ms}$** |
| **95% Quota** | $2.6\text{ ms}$ | **$9.0\text{ ms}$** | $1.989\text{ ms}$ | $1.984\text{ ms}$ | **$1.952\text{ ms}$** |
| **99% Quota** | $2.2\text{ ms}$ | **$6.0\text{ ms}$** | $1.989\text{ ms}$ | $1.984\text{ ms}$ | **$1.952\text{ ms}$** |

### Technical Reasons & Mechanistic Breakdown

1. **Unpredictable Latency Oscillations under CFS Quota Tuning (Panel A)**:
   * Tuning container CPU limits from $80\%$ to $99\%$ does **not** produce monotonic latency improvements. Instead, P99 tail latency fluctuates violently between $6.0\text{ ms}$ and $14.0\text{ ms}$.
   * **Root Cause: CFS Periodic Throttling (`nr_throttled`)**: When a noisy container bursts, it rapidly consumes its assigned CPU quota within the first few milliseconds of the CFS period ($100\text{ ms}$). The kernel's completely fair scheduler throttles the thread for the remaining duration of the period.
   * This periodic pausing and resuming introduces severe jitter. Incoming TCP connections stall inside kernel socket queues waiting for the throttled process to be descheduled and rescheduled, causing arbitrary tail spikes (up to $14.0\text{ ms}$ at 90% quota).
2. **Deterministic Hierarchy Among eBPF QoS Solutions (Panel B)**:
   * In Panel B, high-resolution magnification ($1.92\text{--}2.02\text{ ms}$) reveals the precise performance hierarchy:
     * **QoS Dynamic ($1.989\text{ ms}$, Cyan Diamonds)**: Slightly higher tail latency due to the $\Delta t$ latency required for user-space control loop sampling and adaptive rate updates.
     * **QoS Tiered ($1.984\text{ ms}$, Blue Triangles)**: Achieves slightly lower tail latency than dynamic because classification rules are baked into the static in-kernel BPF bytecode, avoiding user-space polling overhead.
     * **QoS Markov Predictive ($1.952\text{ ms}$, Emerald Circles)**: Yields the absolute lowest tail latency among all configurations, proactively throttling upcoming bursts based on stochastic transition probabilities ($P_{\text{spike}} \ge 0.60$) before packet buffers queue up.
   * Crucially, because all three QoS architectures operate at the kernel TC boundary rather than inside container cgroups, their data points remain invariant and immune to the container CPU limits that destabilize Cgroups.

---

## 4. Graph 3: Tail Inflation Analysis across Percentile Distributions

![Graph 3: Tail Inflation Analysis](file:///home/udeepa/CLB/test_bed/plots/cgroups_vs_qos_percentiles_progression.png)

* **Plot Location**: [`/home/udeepa/CLB/test_bed/plots/cgroups_vs_qos_percentiles_progression.png`](file:///home/udeepa/CLB/test_bed/plots/cgroups_vs_qos_percentiles_progression.png)

### Benchmark Data (Percentile Distribution Progression)

| Percentile Metric | Cgroups (CFS Quota Mean) | QoS Tiered (Static eBPF) | QoS Markov (DTMC Predictive) | Cgroups Tail Degradation Factor |
| :--- | :---: | :---: | :---: | :---: |
| **P50 (Median)** | $2.8\text{ ms}$ | $0.9\text{ ms}$ | **$0.8\text{ ms}$** | $3.5\times$ higher |
| **P90** | $4.2\text{ ms}$ | $1.8\text{ ms}$ | **$1.2\text{ ms}$** | $3.5\times$ higher |
| **P99 (Tail)** | $9.0\text{ ms}$ | $2.0\text{ ms}$ | **$2.0\text{ ms}$** | $4.5\times$ higher |
| **P99.9 (Extreme Tail)** | **$21.6\text{ ms}$** | $7.0\text{ ms}$ | **$4.7\text{ ms}$** | **$4.6\times$ higher** |

### Technical Reasons & Mechanistic Breakdown

1. **Extreme Tail Explosion in Cgroups ($2.8\text{ ms} \to 21.6\text{ ms}$)**:
   * While Cgroups maintain a reasonable P50 median latency ($2.8\text{ ms}$), the tail distribution experiences catastrophic inflation, surging to $9.0\text{ ms}$ at P99 and exploding to **$21.6\text{ ms}$ at P99.9** ($7.7\times$ amplification).
   * **Root Cause: Compounding Head-of-Line Blocking**:
     1. Under SoftIRQ contention, TCP ACKs and application read requests suffer packet-scheduling delays.
     2. As soon as the container process hits its CFS quota, it is put to sleep by the scheduler.
     3. Buffered packets accumulate in socket queues. When the container wakes up, it experiences a "thundering herd" of queued requests, compounding latency for requests arriving at the end of the throttle window.
2. **Superiority of DTMC Predictive QoS at Extreme Tail ($4.7\text{ ms}$)**:
   * Static Tiered QoS caps P99.9 at $7.0\text{ ms}$.
   * The **DTMC Markov Predictive Controller** achieves the lowest extreme tail latency of all tested architectures at **$4.7\text{ ms}$** ($4.6\times$ lower than Cgroups).
   * Because the Markov engine calculates transition probabilities $P(S_{t+1} \in \{\text{HIGH}, \text{SEVERE}\} \mid S_t)$ online, it throttles incoming bursts *before* queue accumulation occurs, preventing the deep queue backlogs that generate extreme P99.9 tail spikes.

---

## 5. Graph 4: Relative Tail Latency Speedup Factor

![Graph 4: Relative Speedup Factor](file:///home/udeepa/CLB/test_bed/plots/cgroups_vs_qos_improvement_factor.png)

* **Plot Location**: [`/home/udeepa/CLB/test_bed/plots/cgroups_vs_qos_improvement_factor.png`](file:///home/udeepa/CLB/test_bed/plots/cgroups_vs_qos_improvement_factor.png)

### Benchmark Data (Speedup Relative to Live Cgroups 80% Baseline of $2.99\text{ ms}$)

| Policy / Implementation | P99 Tail Latency | Speedup Ratio (Relative to Live Cgroups 80%) | Interpretation |
| :--- | :---: | :---: | :--- |
| **Cgroups 90% Quota (Sweep)** | $14.0\text{ ms}$ | **$0.21\times$** | $4.7\times$ worse than live baseline due to CFS queue stalls |
| **Cgroups 80% Quota (Sweep)** | $10.0\text{ ms}$ | **$0.30\times$** | $3.3\times$ worse than live baseline under sustained flood |
| **Cgroups 80% (Live Parity)** | $2.99\text{ ms}$ | **$1.00\times$** | Reference Baseline ($1.0\times$ parity line) |
| **QoS Dynamic (Stackelberg)** | $1.99\text{ ms}$ | **$1.50\times$** | $50\%$ higher tail responsiveness via dynamic game theory |
| **QoS Tiered (Static eBPF)** | $1.98\text{ ms}$ | **$1.51\times$** | $51\%$ speedup via early ingress packet classification |
| **QoS Markov DTMC (Predictive)** | $1.95\text{ ms}$ | **$1.53\times$** | **$53\%$ speedup** via predictive preemptive rate limiting |

### Technical Reasons & Mechanistic Breakdown

1. **Quantifying the Operational Advantage**:
   * Graph 4 standardizes performance against the live Cgroups 80% quota implementation ($2.99\text{ ms} = 1.0\times$).
   * Under historical sweep conditions where burst duration exacerbated scheduling jitter, Cgroups degraded dramatically to $0.30\times$ and $0.21\times$ the performance of the live baseline.
   * All three eBPF QoS architectures reliably deliver a **$1.50\times$ to $1.53\times$ speedup** over Cgroups under equivalent live conditions.
2. **Why Game Theory and Markov Prediction Deliver the Highest Speedup**:
   * **QoS Dynamic ($1.50\times$)**: Computes the optimal Stackelberg follower response $r^*(H_t)$, guaranteeing that victim flows receive uncapped priority while throttling the attacker smoothly along a sigmoid curve.
   * **QoS Markov ($1.53\times$)**: Surpasses purely reactive mechanisms by initiating rate adjustments one control cycle early whenever transition probability $P_{\text{spike}} \ge 0.60$, preventing transient latency dips before they register.

---

## 6. Synthesis: Why Cgroups Cannot Replace Data-Plane eBPF QoS

The empirical results across all 4 graphs establish clear conclusions for service mesh design:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Layer of Execution                              │
├────────────────────────────────────────────────────────────────────────┤
│  User Space (Cgroups v2)                                              │
│  • Manages CFS runqueue task time slices (cpu.cfs_quota_us).           │
│  • Blind to in-kernel SoftIRQ packet arrival.                          │
│  • Induces periodic throttling jitter (nr_throttled) and queue stalls. │
│  • P99 tail latency: 2.93 ms – 14.0 ms (P99.9: 21.6 ms).              │
├────────────────────────────────────────────────────────────────────────┤
│  Kernel Space Fast Path (Proposed eBPF QoS)                           │
│  • Executes at TC ingress (tc clsact) before socket buffer allocation. │
│  • Shards state across CPU cores (PERCPU_HASH) to prevent spinlocks.   │
│  • Enforces nanosecond token buckets (TC_ACT_SHOT / TC_ACT_OK).        │
│  • Preemptively anticipates contention via DTMC Markov engine.         │
│  • P99 tail latency: 1.95 ms – 1.99 ms (P99.9: 4.7 ms).                │
└────────────────────────────────────────────────────────────────────────┘
```

1. **Execution Domain Mismatch**: Cgroups operate on user-space thread scheduling; network noisy-neighbor contention occurs in kernel SoftIRQ packet handling. Attempting to mitigate data-plane contention using user-space CPU quotas is fundamentally misaligned.
2. **Tail Latency Determinism**: Cgroups suffer from erratic latency swings caused by CFS quota exhaustion and quota refresh intervals. In contrast, eBPF QoS rate limiters operate at wire speed, ensuring consistent, sub-2ms P99 latency across all load conditions.
3. **Architectural Imperative**: For sidecarless eBPF service meshes, in-kernel data-plane QoS is not merely an optimization—it is an indispensable prerequisite for multi-tenant isolation.
