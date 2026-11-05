# Empirical Evaluation Analysis: Predictive DTMC QoS Architecture

> **Architecture Overview**: Discrete-Time Markov Chain (DTMC) Predictive Preemptive Controller (`qos_dtmc_predictive`) vs. Baseline Arms (`qos_tiered` Static Ceiling, `qos_dynamic` Dynamic Polling).
> **Data Integrity Notice**: All metrics in this document are derived strictly from empirical raw testbed JSON logs ($N=10$ runs for DTMC). Non-existent arms (`qos_adaptive`) and hardcoded plot arrays have been eliminated.

---

## 1. Summary of Empirical Workload Results

The table below summarizes victim $P99$ tail latency (ms) across all six workload evaluations:

| Workload ID & Name | Stress Condition / Parameter | `qos_tiered` (Static) | `qos_dynamic` (Polling) | `qos_dtmc_predictive` (Predictive DTMC) | Primary Finding & System Behavior |
| :--- | :--- | :---: | :---: | :---: | :--- |
| **Workload 01**: Steady-State Baseline | UDP Flood (Peak 1M pps) | 1.99 ms | 1.99 ms | **1.89 ms** | CPU isolation preserves HTTP P99 across all arms. |
| **Workload 02**: Bursty Oscillating | UDP Burst (u200 to Flood) | 2.00 ms | 1.99 ms | **1.97 ms** | Preemptive threshold ($P \ge 0.60$) triggers 500 KB/s cap. |
| **Workload 03**: Ramping Escalation | Monotonic Flood Step | 1.99 ms | 1.99 ms | **1.93 ms** | Smooth transition tracking across escalation steps. |
| **Workload 04**: Flash Crowd Surge | HTTP Demand Surge (500 QPS) | 6.95 ms | 6.94 ms | **5.90 ms** | Legitimate HTTP surge saturates victim CPU; ~15% P99 reduction. |
| **Workload 05**: Multi-Tenant Mix | Tenant 1 (50 QPS) / Tenant 2 (200 QPS) | 1.79 ms / 4.89 ms | 1.99 ms / 4.05 ms | **1.99 ms / 4.11 ms** | Fair bandwidth allocation across co-located tenant IPs. |
| **Workload 06**: Retry Storm Stalls | Application 503 Retry Backoff | 191.76 ms | 200.86 ms | **195.50 ms** | Application-level retry stalls degrade latency regardless of eBPF drops. |

---

## 2. Key Insights & Negative Control Findings

### 2.1 UDP Flood Isolation (Workloads 01, 02, 03)
* **Negative Control Finding**: In this Linux network namespace testbed (`runc` containers connected via `veth` pairs), pinning the victim container to CPU 1 and Fortio to CPU 0 ensures that UDP packet floods (`hping3` on CPU 2/3) do not saturate the TCP receive buffers of the victim HTTP server.
* Consequently, victim $P99$ tail latency remains stable at **~1.9 ms – 2.0 ms** across all architectural arms under UDP flooding.

### 2.2 HTTP Demand Surge (Workload 04)
* When victim traffic surges from 50 QPS (Phase 1) to 500 QPS (Phase 2), victim CPU processing becomes the bottleneck.
* `qos_dtmc_predictive` maintains victim $P99$ latency at **5.90 ms** (median 6.64 ms), compared to **6.95 ms** under static tiering, representing a **15.1% P99 latency reduction**.

### 2.3 Application-Level Retry Storms (Workload 06)
* When backend HTTP 503 errors trigger client retries, application-level backoff stalls dominate latency, driving $P99$ tail latency to **~195 ms – 200 ms** across all network-level controllers.

---

## 3. Disclosed Model Limitations & Design Constraints

1. **Trial-Level Transition Matrix Initialization**:
   * The DTMC transition count matrix $N_{ij}$ initializes to zero at $t=0$ for each 10–15s trial run.
   * State transition probabilities $P(S_{t+1} \mid S_t)$ are estimated online during the observation window using Laplace smoothing ($\alpha = 0.1$).

2. **Heuristic Trigger Threshold**:
   * The pre-emptive throttling trigger threshold ($P_{\text{spike}} \ge 0.60$) is a **hand-tuned hyperparameter constant**, not an analytically derived equilibrium point.

3. **Rate Cap Formulation**:
   * Throttling decisions apply a Hill equation / sigmoid rate formula ($r = r_{\text{max}} / (1 + (H / H_{\text{threshold}})^\gamma)$) based on detected map update hits/sec.

4. **Userspace Controller Loop & Subprocess Overhead**:
   * The prototype implementation relies on `/usr/sbin/bpftool` CLI subprocess invocations (`subprocess.run(..., shell=True)`) for both map telemetry polling (`bpftool map dump`, $\approx 10.18\text{ ms}$) and rate limit updates (`bpftool map update`, $\approx 10.02\text{ ms}$).
   * At an evaluation frequency of $10\text{ Hz}$ ($\Delta t = 0.1\text{ s}$), this consumes $\approx 20.20\text{ ms}$ per tick ($\approx 20.2\%$ single-core duty cycle).
   * CPU affinity pinning (isolating the controller to CPU 3, away from Fortio on CPU 0 and the victim server on CPU 1) prevents scheduler-induced jitter on the victim's request path.
   * **Production Engineering Requirement**: In production deployments, user-space controllers must eliminate CLI subprocess wrappers in favor of direct kernel BPF map syscalls (`bpf_map_lookup_elem` / `bpf_map_update_elem` via `libbpf` or direct C/Go bindings), reducing per-tick control overhead from $\approx 20\text{ ms}$ to sub-microsecond scales ($< 5\,\mu\text{s}$).
