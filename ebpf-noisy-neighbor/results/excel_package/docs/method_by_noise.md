# method_by_noise.csv

Data file:
- [../method_by_noise.csv](../method_by_noise.csv)

Purpose:
- Method performance by noise intensity.
- Closely matches the p99-vs-noise plot logic with richer metrics.

## Grouping keys
- isolation_method
- noise_level

## Coverage columns
- samples
- unique_experiments
- unique_tenants

## Metric columns
For each base metric, the table includes _mean, _std, _min, _max:
- p50_ms
- p95_ms
- p99_ms
- jitter_ms
- throughput_rps
- packet_drops
- tail_amplification
- isolation_score

## Relation to test environment
Direct relation to matrix dimensions:
- isolation_method and noise_level are explicit grouping keys.
- Other dimensions are merged inside each row: traffic_pattern, identity_mode, containers, failure_mode, iteration.

## Recommended supervisor views
- Main comparison: p99_ms_mean by noise level for each method.
- Robustness comparison: p99_ms_std by noise level.
- Side metric: throughput_rps_mean to discuss latency-throughput trade-off.