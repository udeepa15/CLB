# method_by_failure_mode.csv

Data file:
- [../method_by_failure_mode.csv](../method_by_failure_mode.csv)

Purpose:
- Resilience-focused summary for injected disturbance modes.

## Grouping keys
- isolation_method
- failure_mode

## Coverage columns
- samples
- unique_experiments
- unique_tenants

## Metric columns
For each base metric, this table provides _mean, _std, _min, _max:
- p50_ms
- p95_ms
- p99_ms
- jitter_ms
- throughput_rps
- packet_drops
- tail_amplification
- isolation_score

## Relation to test environment
- Explicitly maps to failure_mode dimension from [../../../config.yaml](../../../config.yaml): none, spike, churn.
- isolates method behavior under normal vs disturbed conditions.
- merges other dimensions inside each row: noise_level, traffic_pattern, identity_mode, containers, iteration.

## Recommended supervisor views
- Compare p99_ms_mean for spike/churn against none per method.
- Use isolation_score_mean and jitter_ms_mean to discuss control quality during stress.