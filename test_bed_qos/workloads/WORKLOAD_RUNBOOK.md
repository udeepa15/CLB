# Workload Suite Runbook: Standalone Workload Execution Guide

This document provides a complete guide for running each of the 7 realistic workload benchmarks **individually and independently** at $N=10$ repetitions.

Running workloads individually prevents long monolithic benchmark runs from failing due to transient environment disruptions, allows incremental verification of results, and lets you re-run specific workloads without restarting the entire suite.

---

## Quick Reference: Independent Launcher Scripts

All standalone launcher scripts are located in `~/CLB/test_bed_qos/workloads/`.

| Workload | Launcher Command | Expected Runtime (N=10) | Generated Plot Output |
| :--- | :--- | :--- | :--- |
| **01 Steady State** | `sudo bash run_workload_01.sh 10` | ~20 min | `01_steady_state/plots/steady_state_p99.png` |
| **02 Bursty Oscillating** | `sudo bash run_workload_02.sh 10` | ~8 min | `02_bursty_oscillating/plots/bursty_p99_box.png` |
| **03 Ramping Escalation** | `sudo bash run_workload_03.sh 10` | ~8 min | `03_ramping_escalation/plots/ramping_p99_bar.png` |
| **04 Flash Crowd Victim** | `sudo bash run_workload_04.sh 10` | ~8 min | `04_flash_crowd_victim/plots/flash_crowd_phase_p99.png` |
| **05 Multi-Tenant Mix** | `sudo bash run_workload_05.sh 10` | ~8 min | `05_multi_tenant_mix/plots/multitenant_per_tenant_p99.png` |
| **06 Retry Storm** | `sudo bash run_workload_06.sh 10` | ~8 min | `06_retry_storm/plots/retry_storm_amplification.png` |
| **07 Adaptive Attacker** | `sudo bash run_workload_07.sh 10` | ~12 min | `07_adaptive_attacker/plots/adaptive_3arm_p99.png` |

*(Note: You can pass a different repetition count as the argument, e.g. `sudo bash run_workload_01.sh 2` for a quick 2-repetition pilot test).*

---

## Detailed Workload Specifications & Instructions

### 1. Workload 01: Extended 60s Steady-State Control Baseline
- **Command**:
  ```bash
  cd ~/CLB/test_bed_qos/workloads && sudo bash run_workload_01.sh 10
  ```
- **Citation**: Gan et al., *"An Open-Source Benchmark Suite for Microservices"* (ASPLOS 2019, **DeathStarBench**).
- **Parameters**: 60-second trial duration per arm across flood levels (`0`, `u200`, `u20`, `u2`, `u1`, `flood`).
- **Architectures Tested**: `qos_tiered` vs `qos_dynamic`.
- **Expected Outcome**: `qos_tiered` and `qos_dynamic` perform similarly under flat steady-state traffic, as static tiering is optimal for unvarying profiles.
- **Outputs**:
  - Raw JSON/CSV logs: `01_steady_state/results/multi_run_N10_<timestamp>/`
  - Plot: `01_steady_state/plots/steady_state_p99.png`

---

### 2. Workload 02: Bursty Oscillating Attacker Traffic
- **Command**:
  ```bash
  cd ~/CLB/test_bed_qos/workloads && sudo bash run_workload_02.sh 10
  ```
- **Citation**: de Paula Jr. et al., *"Handling Flash-Crowd Events to Improve the Performance of Web Applications"* (2014) & WorldCup98 trace.
- **Parameters**: `BurstyAttackerThread` alternates flood intensity between `u200` (low) and `flood` (max) every 5 seconds.
- **Architectures Tested**: `qos_tiered` vs `qos_dynamic`.
- **Expected Outcome**: `qos_dynamic` rapidly clamps down during high contention bursts and relaxes during low phases, suppressing victim tail latency.
- **Outputs**:
  - Raw JSON/CSV logs: `02_bursty_oscillating/results/multi_run_N10_<timestamp>/`
  - Plot: `02_bursty_oscillating/plots/bursty_p99_box.png`

---

### 3. Workload 03: Ramping Escalation Attacker Traffic
- **Command**:
  ```bash
  cd ~/CLB/test_bed_qos/workloads && sudo bash run_workload_03.sh 10
  ```
- **Citation**: Welsh & Culler, *"Adaptive overload control for busy Internet servers"* (USENIX ITS 2003).
- **Parameters**: `RampingAttackerThread` monotonically steps down `hping3 -i` interval every 2 seconds (`u500` -> `u200` -> `u50` -> `u20` -> `u5` -> `u2` -> `flood`).
- **Architectures Tested**: `qos_tiered` vs `qos_dynamic`.
- **Expected Outcome**: Evaluates control plane reaction lag and latency stabilization under step-ramping overload.
- **Outputs**:
  - Raw JSON/CSV logs: `03_ramping_escalation/results/multi_run_N10_<timestamp>/`
  - Plot: `03_ramping_escalation/plots/ramping_p99_bar.png`

---

### 4. Workload 04: Flash Crowd Victim Demand Surge
- **Command**:
  ```bash
  cd ~/CLB/test_bed_qos/workloads && sudo bash run_workload_04.sh 10
  ```
- **Citation**: de Paula Jr. et al. (2014) & WorldCup98 trace reference.
- **Parameters**: Constant background flood (`u20`), with victim load surging from 50 QPS (first 10s) to 500 QPS (second 10s).
- **Architectures Tested**: `qos_tiered` vs `qos_dynamic`.
- **Expected Outcome**: `qos_dynamic` accommodates legitimate leader demand surge without false-positive dropping.
- **Outputs**:
  - Raw JSON/CSV logs: `04_flash_crowd_victim/results/multi_run_N10_<timestamp>/`
  - Plot: `04_flash_crowd_victim/plots/flash_crowd_phase_p99.png`

---

### 5. Workload 05: Heterogeneous Multi-Tenant Co-location Mix
- **Command**:
  ```bash
  cd ~/CLB/test_bed_qos/workloads && sudo bash run_workload_05.sh 10
  ```
- **Citation**: Alibaba Cluster Trace dataset (Lu et al., 2017).
- **Parameters**: Victim 1 (latency-sensitive 50 QPS) co-located with Victim 2 (background throughput 200 QPS) under targeted noise (`u20` & `u200`).
- **Architectures Tested**: `qos_tiered` vs `qos_dynamic`.
- **Expected Outcome**: `qos_dynamic` dynamically balances rate limiting per tenant based on observed lock contention.
- **Outputs**:
  - Raw JSON/CSV logs: `05_multi_tenant_mix/results/multi_run_N10_<timestamp>/`
  - Plot: `05_multi_tenant_mix/plots/multitenant_per_tenant_p99.png`

---

### 6. Workload 06: Unbounded Retry Storm Amplification
- **Command**:
  ```bash
  cd ~/CLB/test_bed_qos/workloads && sudo bash run_workload_06.sh 10
  ```
- **Citation**: Tavori et al., *"Retry Storms: Amplification & Mitigation"* (arXiv 2025).
- **Parameters**: `victim_server_retry.py` injects 20% 503 errors and 100ms stalls. `retry_client.py` sends requests with zero-backoff retries.
- **Architectures Tested**: `qos_tiered` vs `qos_dynamic`.
- **Expected Outcome**: `qos_dynamic` limits request amplification hitting backend worker threads while capping latency.
- **Outputs**:
  - Raw JSON/CSV logs: `06_retry_storm/results/multi_run_N10_<timestamp>/`
  - Plot: `06_retry_storm/plots/retry_storm_amplification.png`

---

### 7. Workload 07: Rational Adaptive Adversary (3-Way Ablation Study)
- **Command**:
  ```bash
  cd ~/CLB/test_bed_qos/workloads && sudo bash run_workload_07.sh 10
  ```
- **Citation**: He et al., *"A Game-Theoretical Approach for Mitigating Edge DDoS Attack"* (IEEE TDSC 2021).
- **Parameters**: `AdaptiveAttackerThread` polls rate limits every 1s and adjusts flood strategy.
- **3-Arm Ablation**:
  1. `qos_tiered` (Static priority boundary)
  2. `qos_proportional` (Linear feedback control: `Limit = Baseline - Kp * Hits`)
  3. `qos_dynamic` (Stackelberg Game Leader-Follower Equilibrium Solver)
- **Expected Outcome**: `qos_dynamic` reaches a stable Stackelberg equilibrium, whereas `qos_proportional` oscillates unstably and `qos_tiered` fails to adapt.
- **Outputs**:
  - Raw JSON/CSV logs: `07_adaptive_attacker/results/multi_run_N10_<timestamp>/`
  - Plot: `07_adaptive_attacker/plots/adaptive_3arm_p99.png`

---

## Best Practices & Troubleshooting

1. **Running inside `tmux`**: Always launch your terminal session in `tmux` so that runs continue seamlessly even if your SSH session closes:
   ```bash
   tmux new -s workload_n10
   ```
2. **Re-running a Failed Workload**: If a specific workload run is interrupted, simply re-run its standalone script (e.g. `sudo bash run_workload_06.sh 10`). Existing results from other workloads are completely unaffected.
3. **Analyzing Results Manually**: You can re-run the analysis script for any workload at any time without re-executing the benchmarks:
   ```bash
   cd ~/CLB/test_bed_qos/workloads/01_steady_state && python3 analyze.py
   ```
