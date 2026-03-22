# method_by_traffic_pattern.csv

Data file:
- [../method_by_traffic_pattern.csv](../method_by_traffic_pattern.csv)

Purpose:
- Method sensitivity analysis by traffic pattern.

## Grouping keys
- isolation_method
- traffic_pattern

## Coverage columns
- samples
- unique_experiments
- unique_tenants

## Metric columns
For each base metric, this file includes _mean, _std, _min, _max:
- p50_ms
- p95_ms
- p99_ms
- jitter_ms
- throughput_rps
- packet_drops
- tail_amplification
- isolation_score

## Relation to test environment
- Explicitly maps to traffic_pattern from [../../../config.yaml](../../../config.yaml): constant, bursty, delayed_start, intermittent.
- merges other environment dimensions inside each row: noise_level, identity_mode, containers, failure_mode, iteration.

## Recommended supervisor views
- Compare p99_ms_mean by pattern to show pattern-specific weakness/strength.
- Use jitter_ms_mean to discuss burst sensitivity.
- Use tail_amplification_mean for tail-risk behavior under each pattern.