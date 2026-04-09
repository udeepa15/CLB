# Conclusion

## Executive Summary
Based on the processed results and generated plots, the eBPF/tc isolation approaches improve latency under low-noise conditions, but all methods converge to similarly high tail latency under high-noise stress. In the current dataset, no method is consistently dominant across all regimes; method effectiveness is strongly condition-dependent.

Primary evidence files:
- [../results/processed/results.csv](../results/processed/results.csv)
- [../results/processed/summary.csv](../results/processed/summary.csv)
- [../analysis/plot.py](../analysis/plot.py)

## Main Findings

### 1) Low-noise regime shows clear isolation benefit
From the method-by-noise aggregation:
- none: avg p99 = 2.983 ms (n=329)
- tc: avg p99 = 1.936 ms (n=40)
- ebpf: avg p99 = 2.027 ms (n=40)
- adaptive: avg p99 = 1.612 ms (n=32)

Interpretation:
- At low noise, isolation improves tail latency versus no isolation.
- In this dataset, adaptive gives the lowest low-noise p99.

### 2) Medium-noise regime is nearly tied across tc/ebpf/adaptive
For medium noise:
- tc: 4.480 ms
- ebpf: 4.512 ms
- adaptive: 4.712 ms
- none: 4.770 ms

Interpretation:
- Isolation methods remain slightly better than none, but gains are modest in medium noise.

### 3) High-noise regime saturates and differences narrow
For high noise:
- tc: 9.406 ms
- ebpf: 9.633 ms
- none: 9.638 ms
- adaptive: 10.099 ms

Interpretation:
- Under high noise, all methods show high p99 values.
- Adaptive is worst in the current high-noise runs, suggesting the current controller settings may be overreacting or not tuned for severe interference.

### 4) Tail-amplification bars indicate heavier tails for isolated methods in aggregate
Aggregate tail amplification (p99/p50):
- none: 4.985
- tc: 6.381
- ebpf: 6.480
- adaptive: 6.773

Interpretation:
- In pooled aggregate statistics, isolated methods have larger p99/p50 ratios.
- This does not necessarily contradict low-noise improvements; it indicates that mixed stress scenarios create heavier tails and should be analyzed per scenario bucket.

### 5) Legacy baseline vs eBPF comparison shows strong improvement
From the first baseline/ebpf rows in [../results/processed/summary.csv](../results/processed/summary.csv):
- tenant1: 6.190 -> 3.800 ms (38.6% p99 reduction)
- tenant2: 9.824 -> 4.423 ms (55.0% p99 reduction)

Interpretation:
- eBPF can significantly reduce noisy-neighbor impact in focused two-tenant comparisons.

## Important Validity Notes

### 1) The dataset is not fully balanced across methods
Method coverage in [../results/processed/results.csv](../results/processed/results.csv):
- none: 393 rows
- tc: 104 rows
- ebpf: 104 rows
- adaptive: 96 rows

### 2) Configuration coverage differs by method
- tc, ebpf, adaptive are almost entirely container_count=3
- none includes container_count=3,5,10 (extra stress surface)
- adaptive includes identity_mode ip and cgroup
- tc/ebpf/none are mostly ip-only in this dataset
- ebpf uses only strategy=rate_limit in current runs; adaptive uses strategy=adaptive

Implication:
- Global averages should be treated as directional, not final head-to-head proof.
- Noise-stratified or scenario-matched comparisons are more reliable than full pooled averages.

## Final Conclusion
The current results support the claim that traffic isolation (tc/eBPF/adaptive) mitigates noisy-neighbor interference effectively in low-noise and some moderate-noise settings. However, under high-noise stress, performance degrades similarly for all methods, and the adaptive controller (as currently tuned) does not outperform static methods. Therefore, the strongest defensible conclusion is:

- isolation helps substantially in mild-to-moderate interference,
- high-noise resilience remains an open optimization problem,
- and adaptive policy tuning is the highest-impact next step for improving robustness.

## Recommended Next Experimental Step
To produce publication-strength comparative conclusions, run a balanced matrix where all methods share identical scenario coverage (same noise, traffic pattern, identity mode, container count, failure mode, and iteration count), then report confidence intervals per matched bucket.