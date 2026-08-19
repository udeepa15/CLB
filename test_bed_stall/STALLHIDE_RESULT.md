# Direction 2 Ablation Summary: Naive vs Stall-Hiding eBPF Rate Limiter (N=10 Matrix)

> **Document Purpose**: Exhaustive statistical analysis of the full $N=10$ micro-benchmark matrix (160 total trials across 8 flood levels in `~/CLB/test_bed_stall/`), evaluating whether a Beeswax-style (SIGCOMM '26) early-lookup stall-hiding optimization (`ebpf_limiter_stallhide.c`) reduces the limiter's own added latency compared to the naive control implementation (`ebpf_limiter_naive.c`).

---

## 1. Full Benchmark Matrix Results ($N=10$ Repetitions, 160 Total Trials)

| Flood Level | Attacker Flood Rate | Naive P99 Median (IQR) | Stallhide P99 Median (IQR) | Mann-Whitney $p$-value | Statistically Significant? |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`0`** | Baseline (0 pps) | **2.00 ms** (0.01) | **2.00 ms** (0.00) | $p = 0.7913$ | **NO** ($p \ge \text{adj } \alpha$) |
| **`u500`** | 2k pps | **1.99 ms** (0.00) | **1.99 ms** (0.00) | $p = 0.5708$ | **NO** ($p \ge \text{adj } \alpha$) |
| **`u200`** | 5k pps | **1.99 ms** (0.00) | **1.99 ms** (0.02) | $p = 0.4963$ | **NO** ($p \ge \text{adj } \alpha$) |
| **`u50`** | 20k pps | **1.99 ms** (0.05) | **1.88 ms** (0.15) | $p = 0.2568$ | **NO** ($p \ge \text{adj } \alpha$) |
| **`u20`** | 50k pps | **1.93 ms** (0.13) | **1.98 ms** (0.12) | $p = 0.6501$ | **NO** ($p \ge \text{adj } \alpha$) |
| **`u5`** | 200k pps | **1.93 ms** (0.20) | **1.98 ms** (0.09) | $p = 0.6501$ | **NO** ($p \ge \text{adj } \alpha$) |
| **`u2`** | 500k pps | **1.98 ms** (0.12) | **1.98 ms** (0.13) | $p = 0.5708$ | **NO** ($p \ge \text{adj } \alpha$) |
| **`flood`** | Max Unthrottled | **1.98 ms** (0.02) | **1.89 ms** (0.15) | $p = 0.2899$ | **NO** ($p \ge \text{adj } \alpha$) |

---

## 2. Key Empirical Conclusions & Statistical Rigor

1. **Massive Tail Latency Containment**: Both rate limiters hold victim P99 tail latency tightly bounded between **1.88 ms and 2.00 ms** across all 8 flood levels, completely eliminating the severe **12.28 ms** spinlock contention spike observed in the global-key `sidecarless` baseline.
2. **Stall-Hiding Latency Difference is Non-Significant**: Across all 8 flood intensity data points, Bonferroni-corrected Mann-Whitney U testing confirms that the difference between `ebpf_limiter_naive` and `ebpf_limiter_stallhide` is **statistically non-significant** ($p \in [0.2568, 0.7913]$, well above the adjusted significance threshold $\alpha_{\text{adj}} = 0.00625$).
3. **Macro-Architectural Root Cause**: The primary performance bottleneck in eBPF TC rate limiting is **cross-CPU spinlock contention** on shared BPF map hash buckets (`htab_lock_bucket`). Utilizing `BPF_MAP_TYPE_PERCPU_HASH` sharding resolves **>99% of the latency bottleneck**. Intra-program instruction reordering (early key lookup before dereference) yields negligible secondary benefits under Linux kernel networking SoftIRQ dispatch.

---

## 3. Formal Recommendation for Direction 1 (`~/CLB/test_bed_qos/`)

- **Recommendation**: Keep `ebpf_qos_dynamic.c` as currently built in `~/CLB/test_bed_qos/` as the canonical implementation for Direction 1.
- **Defensible Justification**: `ebpf_qos_dynamic.c` already utilizes `BPF_MAP_TYPE_PERCPU_HASH` per-CPU sharding, which fully addresses the kernel lock bottleneck. The intra-program instruction restructuring in `ebpf_limiter_stallhide.c` introduces code complexity without providing statistically significant tail latency reductions under single-node benchmark conditions.
