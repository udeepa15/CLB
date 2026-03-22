# top_200_worst_p99_cases.csv

Data file:
- [../top_200_worst_p99_cases.csv](../top_200_worst_p99_cases.csv)

Purpose:
- Focused worst-case table for tail-latency forensic analysis.
- Contains the top 200 samples sorted by highest p99_ms.

## Columns
- timestamp
- experiment
- tenant
- noise_level
- traffic_pattern
- isolation_method
- identity_mode
- strategy
- containers
- failure_mode
- p50_ms
- p95_ms
- p99_ms
- jitter_ms
- throughput_rps
- tail_amplification
- isolation_score

## Relation to test environment
- Keeps key scenario dimensions (noise_level, traffic_pattern, isolation_method, identity_mode, containers, failure_mode).
- Enables direct attribution of extreme tails to specific environment conditions.

## Recommended supervisor views
- Group by isolation_method to see which methods dominate worst-case list.
- Group by noise_level and failure_mode to identify stress amplifiers.
- Cross-check experiments against [experiment_rollup.csv](../experiment_rollup.csv).