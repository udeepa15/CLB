# experiment_rollup.csv

Data file:
- [../experiment_rollup.csv](../experiment_rollup.csv)

Purpose:
- One-row rollup per experiment id.
- Preserves scenario identity while summarizing tenant-level sample metrics.

## Scenario identity columns
- experiment
- timestamp_first
- noise_level
- traffic_pattern
- isolation_method
- identity_mode
- strategy
- containers
- failure_mode
- iteration

## Coverage columns
- samples: total sample rows for this experiment.
- unique_tenants: tenant count represented in experiment.

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
- Directly corresponds to one concrete scenario instance from your matrix.
- Best file for tracing values back to raw logs and per-experiment artifacts.

## Recommended supervisor views
- Filter by one method and compare experiment-to-experiment variability.
- Sort by p99_ms_mean descending to identify problematic scenarios.
- Use identity_mode and strategy columns to compare control approaches.