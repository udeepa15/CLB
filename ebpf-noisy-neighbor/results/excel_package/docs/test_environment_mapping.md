# Test Environment Mapping

This document maps dataset values to your experiment environment dimensions.

Configuration source:
- [../../../config.yaml](../../../config.yaml)

## Global controls
- iterations: number of repeated runs per scenario.
- requests_per_client: requests generated per client run.
- p99_threshold_ms: adaptive controller decision threshold.
- sample_interval_s: sampling interval for monitoring/controller logic.

## Matrix dimensions and dataset columns
- noise_level -> column: noise_level
  - values: low, medium, high
- traffic_pattern -> column: traffic_pattern
  - values: constant, bursty, delayed_start, intermittent
- isolation_method -> column: isolation_method
  - values: none, tc, ebpf, adaptive
- identity_mode -> column: identity_mode
  - values: ip, cgroup
- container_count -> column: containers
  - values: 3, 5, 10
- ebpf_strategies -> column: strategy
  - values observed: none, rate_limit, adaptive
- failure_mode -> column: failure_mode
  - values: none, spike, churn

## Additional environment-related identifiers
- experiment: encoded scenario id including noise, pattern, method, identity mode, container count, failure mode, iteration.
- tenant: tenant identifier measured for that sample.
- iteration: run repeat index.
- timestamp: execution time of the sample.

## Metric families
Latency and stability metrics:
- p50_ms, p95_ms, p99_ms
- jitter_ms
- variance_ms2
- tail_amplification

Load and reliability metrics:
- throughput_rps
- packet_drops
- failures
- recovery_window_samples
- degradation_window_samples

Composite metric:
- isolation_score

## Important interpretation note
Some summaries aggregate across multiple dimensions. For strict method comparisons, filter to matched subsets (same noise_level, traffic_pattern, containers, identity_mode, and failure_mode).