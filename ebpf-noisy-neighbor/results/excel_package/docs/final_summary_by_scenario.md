# final_summary_by_scenario.csv

Data file:
- [../final_summary_by_scenario.csv](../final_summary_by_scenario.csv)

Purpose:
- Publication-style scenario summary table with uncertainty fields.
- Each row summarizes one scenario bucket.

## Columns
- noise_level: scenario noise level.
- traffic_pattern: scenario traffic shape.
- isolation_method: scenario method.
- identity_mode: scenario identity mode.
- containers: scenario container count.
- runs: number of run observations in the bucket.
- avg_p99_ms: average p99 latency for bucket.
- stddev_p99_ms: standard deviation of p99 latency.
- ci95_p99_ms: 95% confidence interval half-width for p99 mean.

## Relation to test environment
This file is grouped by environment dimensions from [../../../config.yaml](../../../config.yaml):
- noise_level
- traffic_pattern
- isolation_method
- identity_mode
- containers

## Recommended supervisor views
- Compare avg_p99_ms across methods within the same noise_level and traffic_pattern.
- Use ci95_p99_ms to discuss confidence and variability.
- Flag rows with low runs as preliminary evidence.