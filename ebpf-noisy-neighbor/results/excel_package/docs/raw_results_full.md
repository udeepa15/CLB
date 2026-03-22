# raw_results_full.csv

Data file:
- [../raw_results_full.csv](../raw_results_full.csv)

Purpose:
- This is the full row-level test dataset used for all downstream summaries.
- Each row corresponds to one measured tenant sample for one experiment iteration.

## Columns
- timestamp: sample timestamp.
- experiment: scenario id string.
- noise_level: environment noise level.
- traffic_pattern: noisy traffic shape.
- isolation_method: isolation policy used.
- identity_mode: policy identity key (ip or cgroup).
- ebpf_enabled: whether eBPF data plane was enabled.
- strategy: specific method strategy.
- containers: number of containers in scenario.
- iteration: repeat run number.
- failure_mode: injected failure type.
- tenant: tenant name/id for this row.
- requests: number of requests attempted.
- failures: request failures count.
- p50_ms: median latency in ms.
- p95_ms: 95th percentile latency in ms.
- p99_ms: 99th percentile latency in ms.
- jitter_ms: latency jitter metric in ms.
- variance_ms2: latency variance in ms^2.
- throughput_rps: throughput in requests/second.
- packet_drops: observed packet drop metric.
- tail_amplification: p99 divided by p50.
- recovery_window_samples: post-event recovery window size in samples.
- degradation_window_samples: degradation window size in samples.
- isolation_score: composite isolation performance score.

## Relation to test environment
Direct mapping from [test_environment_mapping.md](test_environment_mapping.md):
- noise_level, traffic_pattern, isolation_method, identity_mode, containers, strategy, failure_mode, iteration.
- requests aligns with requests_per_client from [../../../config.yaml](../../../config.yaml) unless overridden during runs.

## Recommended supervisor views
- Pivot by isolation_method and noise_level on p99_ms.
- Filter failure_mode=spike or churn to show resilience behavior.
- Sort descending by p99_ms to inspect worst-case samples.