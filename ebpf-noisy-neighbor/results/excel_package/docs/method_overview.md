# method_overview.csv

Data file:
- [../method_overview.csv](../method_overview.csv)

Purpose:
- High-level method comparison table (one row per isolation_method).
- Useful as the first summary tab for supervisor discussion.

## Grouping keys
- isolation_method

## Coverage columns
- samples: row count aggregated from raw_results_full.
- unique_experiments: distinct experiment ids represented.
- unique_tenants: distinct tenants represented.

## Metric columns
For each base metric below, four aggregate columns are present:
- suffix _mean: arithmetic mean
- suffix _std: standard deviation
- suffix _min: minimum
- suffix _max: maximum

Base metrics included:
- p50_ms
- p95_ms
- p99_ms
- jitter_ms
- throughput_rps
- packet_drops
- tail_amplification
- isolation_score

## Relation to test environment
- Collapses all environment dimensions into one row per method.
- Dimensions merged inside each row: noise_level, traffic_pattern, identity_mode, containers, failure_mode, iteration.

## Recommended supervisor views
- Compare p99_ms_mean and tail_amplification_mean across methods.
- Check p99_ms_std for method stability.
- Use samples and unique_experiments to explain evidence volume.