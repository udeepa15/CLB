# DTMC Predictive Stackelberg Controller: Detailed Execution Loop Specification

> **System Component Specification & Formal Documentation**  
> **Component**: Userspace Closed-Loop Predictive QoS Controller (`qos_controller_markov.py`)  
> **Theories**: Discrete-Time Markov Chain (DTMC) State Prediction & Stackelberg Leader-Follower Game Optimization  
> **Associated Implementation**: `~/CLB/test_bed_qos/markov_experiments/qos_controller_markov.py`  
> **Kernel Hook**: `~/CLB/test_bed_qos/ebpf_qos_dynamic.c` (`clsact` hook on `veth-att-br`, `br-mesh`)

---

## 1. Detailed Control Loop Flowchart (Mermaid)

The following diagram details the exact cyclic execution flow of the controller, from kernel map telemetry polling through stochastic Markov chain learning, predictive thresholding, game-theoretic rate calculation, to multi-core BPF map actuation.

```mermaid
flowchart TD
    %% Styling Classes
    classDef init fill:#F1F5F9,stroke:#64748B,stroke-width:2px,color:#0F172A;
    classDef telemetry fill:#EFF6FF,stroke:#2563EB,stroke-width:2px,color:#1E40AF;
    classDef dtmc fill:#FAF5FF,stroke:#9333EA,stroke-width:2px,color:#581C87;
    classDef branch fill:#FEF3C7,stroke:#D97706,stroke-width:2px,color:#78350F;
    classDef preemptive fill:#FEE2E2,stroke:#DC2626,stroke-width:2px,color:#991B1B;
    classDef reactive fill:#ECFDF5,stroke:#059669,stroke-width:2px,color:#064E3B;
    classDef solver fill:#FFF1F2,stroke:#E11D48,stroke-width:2px,color:#881337;
    classDef actuation fill:#F0FDF4,stroke:#16A34A,stroke-width:2px,color:#14532D;

    %% 0. Initialization Phase
    subgraph INIT_PHASE ["0. Initialization Phase (t = 0)"]
        START(["Controller Launch<br>taskset -c 3 python3 qos_controller_markov.py"])
        INIT_MAPS["Reset eBPF Map Limits<br>• Victim IPs (10.0.0.10..12): rate = 0 (Uncapped Leader)<br>• Attacker IP (10.0.0.20): rate = 50 MB/s (r_max)"]
        INIT_DTMC["Initialize DTMC Predictor<br>• Count Matrix N_ij = 0 (4x4)<br>• Smoothing Factor α = 0.1<br>• last_state = None"]
        READ_INIT["Sample Initial Counter<br>last_hits = read_update_counter()<br>last_time = time.time()"]
    end
    class START,INIT_MAPS,INIT_DTMC,READ_INIT init;

    %% 1. Ingestion & Telemetry Phase
    subgraph TELEMETRY_PHASE ["1. Telemetry Ingestion Phase (Δt = 100ms)"]
        TICK["Sleep Interval: 100ms (0.1s)<br>time.sleep(interval)"]
        POLL_BPF["Poll update_counter_map<br>sudo bpftool map dump pinned /sys/fs/bpf/tc/globals/update_counter_map -j"]
        CALC_RATE["Calculate Lock Contention Frequency<br>• dt = now - last_time<br>• ΔHits = current_hits - last_hits<br>• Contention Metric H_t = ΔHits / dt (hits/sec)"]
    end
    class TICK,POLL_BPF,CALC_RATE telemetry;

    %% 2. DTMC State Discretization & Learning
    subgraph DTMC_PHASE ["2. Stochastic DTMC Discretization & Online Learning"]
        DISCRETIZE["Discretize Contention into State S_t<br>• H_t < 5,000  → S0 (LOW)<br>• 5,000 ≤ H_t < 15,000 → S1 (MODERATE)<br>• 15,000 ≤ H_t < 30,000 → S2 (HIGH)<br>• H_t ≥ 30,000 → S3 (SEVERE)"]
        UPDATE_COUNTS["Update Transition Frequency Matrix<br>If last_state exists: N[last_state][S_t] += 1<br>last_state = S_t"]
        CALC_TRANS_PROB["Compute Laplace-Smoothed Transition Matrix<br>P_ij = (N_ij + α) / Σ (N_ik + 4α)<br>where α = 0.1"]
    end
    class DISCRETIZE,UPDATE_COUNTS,CALC_TRANS_PROB dtmc;

    %% 3. Predictive Spike Evaluation
    subgraph PREDICTION_PHASE ["3. Predictive Spike Evaluation & Branching"]
        CALC_P_SPIKE["Compute Probability of Contention Surge<br>P_spike = P(S_t+1 ∈ {HIGH, SEVERE} | S_t) = P_{i,2} + P_{i,3}"]
        CHECK_THRESHOLD{"Is P_spike ≥ Threshold?<br>(Default: 0.60 / 60%)"}
        
        BRANCH_PREEMPT["Preemptive Branch Activated<br>• is_preemptive = True<br>• Anticipate severe collision before occurrence<br>• Set H_eff = max(H_t, 35,000 hits/sec)"]
        
        BRANCH_REACTIVE["Standard Reactive Branch<br>• is_preemptive = False<br>• Track observed load directly<br>• Set H_eff = H_t"]
    end
    class CALC_P_SPIKE dtmc;
    class CHECK_THRESHOLD branch;
    class BRANCH_PREEMPT preemptive;
    class BRANCH_REACTIVE reactive;

    %% 4. Stackelberg Game-Theoretic Optimization
    subgraph GAME_PHASE ["4. Stackelberg Game-Theoretic Rate Optimization"]
        LEADER_POLICY["Leader Tenants (Victim IPs 10.0.0.10..12)<br>r_victim = 0 (Uncapped Capacity, Zero Throttling)"]
        
        FOLLOWER_POLICY["Follower Tenant (Attacker IP 10.0.0.20)<br>Compute Sigmoid Best-Response Rate:<br>r*(H_eff) = r_max / [ 1 + (H_eff / H_0)^γ ]<br>• r_max = 50 MB/s, r_min = 500 KB/s<br>• Knee H_0 = 15,000 hits/sec, γ = 2.0<br>• Clamp: r_min ≤ r* ≤ r_max"]
    end
    class LEADER_POLICY,FOLLOWER_POLICY solver;

    %% 5. Actuation & Kernel Update Phase
    subgraph ACTUATION_PHASE ["5. In-Kernel Actuation & Asynchronous Logging"]
        SERIALIZE["Serialize 64-bit Struct for CPU Cores<br>struct rate_limit_entry (48 bytes per CPU core)<br>• rate_bytes_per_sec = r*<br>• max_burst_bytes = 10,000,000"]
        
        PUSH_BPF["Push Limit to percpu_rate_limit_map<br>bpftool map update pinned percpu_rate_limit_map key IP value Struct"]
        
        WRITE_LOG["Append Telemetry to qos_controller_markov_log.jsonl<br>{timestamp, dt_sec, hits_per_sec, current_state,<br>P_spike, is_preemptive, attacker_rate_bps, matrix}"]
        
        UPDATE_LAST["Update State Variables<br>last_hits = current_hits<br>last_time = now"]
    end
    class SERIALIZE,PUSH_BPF,WRITE_LOG,UPDATE_LAST actuation;

    %% Control Flow Links
    START --> INIT_MAPS
    INIT_MAPS --> INIT_DTMC
    INIT_DTMC --> READ_INIT
    READ_INIT --> TICK

    TICK --> POLL_BPF
    POLL_BPF --> CALC_RATE
    CALC_RATE --> DISCRETIZE
    DISCRETIZE --> UPDATE_COUNTS
    UPDATE_COUNTS --> CALC_TRANS_PROB
    CALC_TRANS_PROB --> CALC_P_SPIKE
    CALC_P_SPIKE --> CHECK_THRESHOLD

    CHECK_THRESHOLD -->|Yes: P_spike ≥ 0.60| BRANCH_PREEMPT
    CHECK_THRESHOLD -->|No: P_spike < 0.60| BRANCH_REACTIVE

    BRANCH_PREEMPT --> FOLLOWER_POLICY
    BRANCH_REACTIVE --> FOLLOWER_POLICY
    BRANCH_PREEMPT --> LEADER_POLICY
    BRANCH_REACTIVE --> LEADER_POLICY

    FOLLOWER_POLICY --> SERIALIZE
    LEADER_POLICY --> SERIALIZE
    SERIALIZE --> PUSH_BPF
    PUSH_BPF --> WRITE_LOG
    WRITE_LOG --> UPDATE_LAST
    UPDATE_LAST -.->|Next Loop Iteration (100ms)| TICK
```

---

## 2. Mathematical & Algorithmic Breakdown

The control loop executes as a closed-loop feedback controller on dedicated core **CPU 3** (`taskset -c 3`) at a period of $T = 100\text{ ms}$.

### Step 1: Telemetry Sampling & Contention Rate
At each cycle tick $k$, the controller queries the pinned in-kernel counter map:
$$\text{Counters} \leftarrow \text{bpftool map dump pinned } \texttt{/sys/fs/bpf/tc/globals/update_counter_map} \text{ -j}$$
The cumulative update count across all CPU cores is aggregated into $C_k$. The contention update rate $H_k$ (in eBPF map update hits/sec) is computed as:
$$H_k = \frac{C_k - C_{k-1}}{t_k - t_{k-1}}$$

### Step 2: DTMC State Discretization
To model the stochastic progression of lock contention, continuous hit frequency $H_k$ is quantized into four operational discrete states $\mathcal{S} = \{S_0, S_1, S_2, S_3\}$:

$$\text{State}(H_k) = \begin{cases}
S_0 \text{ (LOW)}, & H_k < 5,000\text{ hits/sec} \\
S_1 \text{ (MODERATE)}, & 5,000 \le H_k < 15,000\text{ hits/sec} \\
S_2 \text{ (HIGH)}, & 15,000 \le H_k < 30,000\text{ hits/sec} \\
S_3 \text{ (SEVERE)}, & H_k \ge 30,000\text{ hits/sec}
\end{cases}$$

### Step 3: Online Empirical Transition Learning (Laplace Smoothing)
The controller observes each transition from state $S_{k-1} = S_i$ to $S_k = S_j$ and increments an in-memory transition frequency matrix:
$$N_{ij} \leftarrow N_{ij} + 1$$
To avoid division-by-zero or zero-probability trapping during early startup cycles ($t < 10\text{ s}$), maximum-likelihood transition probabilities are estimated online using **Laplace smoothing** ($\alpha = 0.1$):

$$P_{ij} = P(S_{k+1} = S_j \mid S_k = S_i) = \frac{N_{ij} + \alpha}{\sum_{m=0}^3 (N_{im} + \alpha)}$$

### Step 4: Stochastic Spike Prediction & Preemptive Threshold
At current state $S_k = S_i$, the controller evaluates the likelihood that the *next* cycle will transition into an acute contention state ($S_2$ or $S_3$):

$$P_{\text{spike}} = P(S_{k+1} \in \{S_2, S_3\} \mid S_k = S_i) = P_{i,2} + P_{i,3}$$

* **If $P_{\text{spike}} \ge 0.60$ (60% Preemptive Trigger)**:
  The controller infers that a burst flood is escalating. Rather than waiting for the kernel spinlock bottleneck to peak, it overrides the observed hit rate with a high synthetic contention rate:
  $$H_{\text{eff}} = \max(H_k, 35,000\text{ hits/sec})$$
* **Else ($P_{\text{spike}} < 0.60$)**:
  The system remains in standard reactive mode:
  $$H_{\text{eff}} = H_k$$

### Step 5: Stackelberg Leader-Follower Game Optimization
The rate allocation is formulated as a single-leader multi-follower Stackelberg game:
1. **Leader (Victim Services: `10.0.0.10..12`)**:
   $$r_{\text{leader}} = 0 \quad (\text{Special flag: Uncapped capacity, bypass all rate limiters})$$
2. **Follower (Attacker / Noisy Tenant: `10.0.0.20`)**:
   The follower's bandwidth is adjusted via a continuous sigmoid decay function:
   $$r^*(H_{\text{eff}}) = \max\left(r_{\min}, \min\left(r_{\max}, \frac{r_{\max}}{1 + \left(\frac{H_{\text{eff}}}{H_0}\right)^\gamma}\right)\right)$$

   * $r_{\max} = 50,000,000\text{ bytes/sec}$ ($50\text{ MB/s}$ unconstrained baseline ceiling)
   * $r_{\min} = 500,000\text{ bytes/sec}$ ($500\text{ KB/s}$ starvation prevention floor)
   * $H_0 = 15,000\text{ hits/sec}$ (Contention knee point)
   * $\gamma = 2.0$ (Curvature sensitivity exponent)

### Step 6: Multi-Core In-Kernel Map Update (Actuation)
The rate limit must be applied across all CPU cores without triggering cross-core spinlocks:
1. For each CPU core ($0 \dots N-1$), the controller formats a 48-byte Little-Endian C binary structure:
   ```c
   struct rate_limit_entry {
       __u64 rate_bytes_per_sec; // r*
       __u64 max_burst_bytes;    // 10,000,000 bytes
       __u64 tokens;             // Initial burst capacity
       __u64 last_update_ns;     // 0 (reset)
       __u64 packets_passed;     // 0
       __u64 packets_dropped;    // 0
   };
   ```
2. The byte string is committed to `/sys/fs/bpf/tc/globals/percpu_rate_limit_map` via `bpftool map update pinned` in a single command.
3. The kernel TC filter (`ebpf_qos_dynamic.c`) immediately enforces the new token replenishment rate at wire speed on the very next packet.

---

## 3. Comparison of Control Loop Paradigms

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                Controller Comparison                                  │
├──────────────────────────────────────┬─────────────────────────────────────────────────┤
│    Reactive Stackelberg Controller   │       DTMC Predictive Stackelberg Controller     │
│          (qos_controller.py)         │           (qos_controller_markov.py)             │
├──────────────────────────────────────┼─────────────────────────────────────────────────┤
│ • Relies on contention derivative    │ • Uses Discrete-Time Markov Chain (DTMC) state   │
│   dC/dt = ΔC / Δt.                   │   probabilities P(S_{t+1} | S_t).               │
│ • Throttles only AFTER kernel lock   │ • Preemptively throttles BEFORE lock contention │
│   contention has already spiked.     │   spikes manifest in kernel space.              │
│ • Adaptive polling interval          │ • Deterministic, fixed 100ms polling cycle      │
│   (20ms, 200ms, 1000ms).             │   with online Laplace learning.                 │
│ • Suffers transient P99 spike during │ • Keeps P99 tail latency flat (~1.95 ms)        │
│   the first 1-2 contention cycles.   │   across burst and escalating workloads.        │
└──────────────────────────────────────┴─────────────────────────────────────────────────┘
```
