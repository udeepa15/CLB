# Methodology and Design Section TODO Resolution Report

> **Document Path**: [`~/CLB/Report/METHODOLOGY_TODO_RESOLUTIONS.md`](file:///home/udeepa/CLB/Report/METHODOLOGY_TODO_RESOLUTIONS.md)  
> **Source File Analyzed**: [`~/CLB/Report/methodology_design.tex`](file:///home/udeepa/CLB/Report/methodology_design.tex)  
> **Purpose**: Provide evidence-based, mathematically honest resolutions and LaTeX wording updates for all `% TODO` doubts in the Methodology and Design chapter.

---

## Executive Resolution Summary

| TODO Line | Section | Original Concern / Doubt | Audit Finding & Mathematical Reality | Recommended Action / Report Wording |
| :---: | :--- | :--- | :--- | :--- |
| **Line 50** | **3.2 System Arch** | Confirm "zero-math" / "lockless" framing against measurements | `BPF_MAP_TYPE_PERCPU_HASH` eliminates cross-CPU `htab_lock_bucket` spinlocks, but has minimal local L1/L2 lookup cost (~0.05ms). | Frame as **"per-CPU lockless map lookup"** (eliminates spinlock contention, negligible cache cost). |
| **Line 97** | **3.3 Rate Limiter** | Confirm Stackelberg derivation vs hand-tuned curve heuristic | **NOT an analytically derived Stackelberg equilibrium.** $r^*(H)$ is a Hill-type sigmoid decay transfer function with hand-tuned empirical parameters ($H_0 = 15k$, $\gamma = 2.0$). | **Re-frame as "adaptive feedback controller informed by leader-follower contention dynamics"** (avoids academic overclaim). |
| **Line 161** | **3.4 Tools Used** | Check if `perf` was used in data collection pipeline | Pipeline grep audit shows telemetry uses `bpftrace`, `mpstat`, eBPF map histograms, and `runc` cgroups. `perf` is not used. | **Remove `perf`** from the tools list in Section 3.4. |
| **Line 167** | **3.5 Preliminary Results** | State explicit $N$ sample sizes for preliminary figures | Early feasibility/ablation used $N=2$ pilots (`run_all_pilots_N2.py`), baseline plots used $N=5$, final matrix used $N=10$. | Explicitly append **"(N=2 pilot runs)"** and **"(N=5 baseline runs)"** to early preliminary findings. |

---

## Detailed Audit & Mathematical Clarifications

### 1. Resolution for TODO #1 (Line 50): Lockless Framing & Lookup Cost

* **LaTeX Location**: Section 3.2 (System Architecture), Line 50.
* **Original TODO**:
  ```latex
  % TODO: confirm the "zero-math" / "lockless" framing against the actual
  % root-cause measurements before final submission -- the lookup still has
  % a small measured cost under contention (see Direction 2 results).
  ```

#### Empirical Evidence & Analysis:
1. **Root-Cause Bottleneck in Baseline**: The baseline eBPF classifier (`ebpf_mesh_router.c`) used a global key (`shared_global_key = 0`) inside a standard `BPF_MAP_TYPE_HASH`. Under concurrent packet arrival across multiple CPU cores, kernel threads blocked on the global bucket spinlock (`htab_lock_bucket`), causing high CPU spin lockup.
2. **Per-CPU Sharding Resolution**: All eBPF rate limiters in `test_bed_qos` (`ebpf_qos_dynamic.c`, `ebpf_qos_tiered.c`) use `BPF_MAP_TYPE_PERCPU_HASH` sharded per CPU core with per-tenant IP keys (`10.0.0.x`).
3. **Lockless vs. Zero-Cost Distinction**:
   * **Lockless**: `BPF_MAP_TYPE_PERCPU_HASH` allocates independent array memory per CPU core. Lookups execute without acquiring cross-CPU spinlocks (`htab_lock_bucket`), making it strictly **cross-core lockless**.
   * **Lookup Overhead**: Direction 2 ablation results ([`images/Naive vs. Stall-Hiding Limiter Latency.png`](file:///home/udeepa/CLB/Report/images/Naive%20vs.%20Stall-Hiding%20Limiter%20Latency.png)) showed that the local memory lookup cost is less than $0.05\text{ ms}$, which is negligible compared to macro queue backpressure.

#### Recommended LaTeX Wording Update:
```latex
Per-tenant token-bucket state is maintained in a \texttt{BPF\_MAP\_TYPE\_PERCPU\_HASH} map. 
By sharding data structures per CPU core, this design eliminates cross-core spinlock 
contention on \texttt{htab\_lock\_bucket} identified during baseline root-cause analysis, 
reducing map access overhead to a negligible per-core L1/L2 cache lookup cost.
```

---

### 2. Rigorous Analysis of TODO #2 (Line 97): Rate Limiter Mathematical Formulation

* **LaTeX Location**: Section 3.3 (The Dynamic Rate Limiter), Line 97.
* **Original TODO**:
  ```latex
  % TODO: this whole subsection assumes the Stackelberg derivation has been
  % confirmed (leader/follower objectives + equilibrium solve, not a
  % hand-tuned curve fit). Do not submit until that verification is done --
  % see supervisor notes. If it turns out to be a tuned heuristic rather
  % than a derived equilibrium, this needs to be reworded to "adaptive
  % feedback controller" throughout, including the figure caption.
  ```

#### Point-by-Point Technical & Mathematical Breakdown:

1. **Explicit Functional Forms of $B_V(r)$ and $C(r, H)$**:
   * **Fact**: There are **no explicit, closed-form algebraic expressions** for $B_V(r)$ or $C(r, H)$ defined in the controller codebase (`qos_controller.py` or `qos_controller_markov.py`). 
   * The docstring in `qos_controller.py` provides an abstract conceptual motivation ($U_L(r) = B_V(r) - C(r, H)$), but no explicit functions for $B_V(r)$ or $C(r, H)$ were ever formulated or differentiated.

2. **Step-by-Step Derivative & Algebra**:
   * **Fact**: The formula used in code:
     $$r^*(H) = \frac{r_{\max}}{1 + \left(\frac{H}{H_{\text{threshold}}}\right)^\gamma}$$
     was **not derived by evaluating $\frac{d U_L}{d r} = 0$**. 
   * Instead, $r^*(H)$ was directly specified as a smooth **Hill-type sigmoid / logistic decay transfer function**, commonly used in control systems to smoothly decay throughput allocation as measured kernel contention $H$ increases.

3. **Origin of $H_{\text{threshold}} = 15,000\text{ hits/sec}$ and $\gamma = 2.0$**:
   * **Fact**: $H_{\text{threshold}} = 15,000\text{ hits/sec}$ and $\gamma = 2.0$ are **hand-tuned empirical hyperparameters**, NOT mathematically solved constants.
   * During baseline testbed profiling (`CLB/test_bed`), empirical measurements of kernel `htab_lock_bucket` wait latency showed an inflection point ("knee") around 15,000 map hits per second where spinlock contention begins escalating exponentially. $\gamma = 2.0$ was selected to provide a smooth quadratic roll-off around this knee point.

4. **Strategic Content of Follower Response $q(r) = r$**:
   * **Fact**: Stating $q(r) = r$ simply reflects an operational assumption of a **saturated sender** (an attacker transmitting at $q_{\text{attack}} \gg r$, so the effective bandwidth admitted by eBPF is bounded by the rate cap $r$). 
   * It is an operational model of a greedy/flooding sender, not a best-response function derived from an explicit follower utility maximization problem.

#### Honest Academic Re-Framing Decision:
Claiming a "solved analytical Stackelberg game equilibrium" is an **overclaim** that would fail under academic committee review. To ensure strict academic integrity and defendability:

1. **Do NOT claim a derived Stackelberg equilibrium.**
2. **Re-frame the controller** as an **adaptive feedback controller informed by leader-follower contention dynamics**.

#### Recommended LaTeX Wording Update for Section 3.3:
```latex
\subsection{The Dynamic Rate Limiter}
The control-plane logic is built around an adaptive feedback controller 
informed by leader-follower contention dynamics. The controller dynamically 
adjusts the bandwidth ceiling of non-legitimate (follower) tenants based on 
real-time kernel contention telemetry.

Bandwidth rate limits $r^*(H)$ are computed using a smooth Hill-type sigmoid decay function:
\begin{equation}
    r^*(H) = \max \left( r_{\min}, \min \left( r_{\max}, \frac{r_{\max}}{1 + \left(\frac{H}{H_0}\right)^\gamma} \right) \right)
\end{equation}
where $r_{\max} = 50\,\text{MB/s}$ is the maximum baseline bandwidth, $r_{\min} = 500\,\text{KB/s}$ 
is the rate floor, $H$ is the observed eBPF map update frequency (hits/sec), and 
$H_0 = 15{,}000\,\text{hits/sec}$ and $\gamma = 2.0$ are empirically tuned hyperparameters 
corresponding to the contention inflection point identified during baseline profiling.

To minimize control-plane overhead, the controller uses an adaptive polling interval 
$dC/dt$ based on the rate of change of kernel contention (Figure \ref{fig:solver}).
```

---

### 3. Resolution for TODO #3 (Line 161): Tools Used Audit (`perf`)

* **LaTeX Location**: Section 3.4 (Tools Used), Line 161.
* **Original TODO**:
  ```latex
  % TODO: perf was listed here previously but doesn't appear anywhere
  % in the actual scripts/pipeline -- remove unless it was genuinely
  % used somewhere and I just forgot to log it.
  ```

#### Codebase Grep Audit Findings:
* Search across `test_bed`, `test_bed_qos`, and `markov_experiments` confirmed that Linux `perf` (`perf_event`) is **NOT** invoked anywhere in data collection shell scripts or python runners.
* Telemetry collection relies on:
  1. `bpftrace`: Tracing `sched_wakeup`, `sched_switch`, and `htab_lock_bucket` kprobes (`collect_bpftrace_lock.bt`).
  2. `mpstat`: CPU core utilization and softirq monitoring (`collect_network_stats.sh`).
  3. `collect_cgroup_stats.py`: `runc` cgroup CPU throttling metrics.
  4. `collect_ebpf_stats.py`: Custom in-kernel eBPF histogram maps (`lock_latency_hist`, `update_counter_map`).

#### Action Required:
* **Remove `perf`** from the `\subsection{Tools Used}` bullet list in `methodology_design.tex`.

---

### 4. Resolution for TODO #4 (Line 167): Sample Size ($N$) Clarification

* **LaTeX Location**: Section 3.5 (Preliminary Results and Discussion), Line 167.
* **Original TODO**:
  ```latex
  % TODO: all numbers/conclusions in this subsection need an explicit N
  % stated next to them. Several of these were N=2 pilot runs, not
  % statistically validated -- say so plainly rather than stating them as
  % findings.
  ```

#### Empirical Sample Size ($N$) Audit:
* **Initial Direction 1 vs Direction 2 Feasibility**: $N=2$ pilot runs (`workloads/run_all_pilots_N2.py`).
* **Baseline Architecture Comparison**: $N=5$ averaged runs ([`images/Sidecar vs Sidecarless eBPF (N=5 Runs).png`](file:///home/udeepa/CLB/Report/images/Sidecar%20vs%20Sidecarless%20eBPF%20(N=5%20Runs).png)).
* **Final Workload Evaluation Matrix**: $N=10$ repetitions (`run_markov_runner.py --reps 10`).

#### Recommended LaTeX Wording Updates in Section 3.5:
```latex
Baseline testing across $N=5$ averaged runs showed a clear isolation problem in the 
unmodified sidecarless mesh: P99 tail latency increased by more than 100\% under 
high-intensity flooding (Figure \ref{fig:baseline_avg}). Scaling the topology up to 
10 nodes ($N=5$ runs) demonstrated that this isolation deficit degrades further as 
noisy neighbors scale (Figure \ref{fig:baseline_nodes}).

Preliminary ablation testing of Direction 1 vs. Direction 2 was conducted using 
initial pilot runs ($N=2$ trials) to guide design selection prior to full-scale 
statistical validation ($N=10$ runs reported in Chapter 4).
```
