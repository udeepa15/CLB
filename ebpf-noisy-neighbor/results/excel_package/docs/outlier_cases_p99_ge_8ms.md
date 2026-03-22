# outlier_cases_p99_ge_8ms.csv

Data file:
- [../outlier_cases_p99_ge_8ms.csv](../outlier_cases_p99_ge_8ms.csv)

Purpose:
- Full outlier subset where p99_ms is greater than or equal to 8 ms.
- Threshold aligns with p99_threshold_ms in [../../../config.yaml](../../../config.yaml).

## Columns
This file retains the full raw schema:
- timestamp
- experiment
- noise_level
- traffic_pattern
- isolation_method
- identity_mode
- ebpf_enabled
- strategy
- containers
- iteration
- failure_mode
- tenant
- requests
- failures
- p50_ms
- p95_ms
- p99_ms
- jitter_ms
- variance_ms2
- throughput_rps
- packet_drops
- tail_amplification
- recovery_window_samples
- degradation_window_samples
- isolation_score

## Relation to test environment
- Includes all environment dimensions and full metrics for only outlier conditions.
- Useful for discussing threshold violations and controller tuning opportunities.

## Recommended supervisor views
- Pivot by isolation_method and failure_mode on outlier count.
- Analyze identity_mode and strategy combinations among outliers.
- Compare with [raw_results_full.csv](../raw_results_full.csv) to compute outlier rate per method.