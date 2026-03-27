# p99 Heatmap by Noise and Traffic

Chart: [p99_heatmap_by_noise_and_traffic.png](p99_heatmap_by_noise_and_traffic.png)

## What this chart shows

Three heatmaps (low/medium/high noise). Each cell is the weighted mean p99 latency (ms) for a `(traffic_pattern, isolation_method)` pair.

Lower values are better.

## Data source and test configuration

Source: [results/excel_package/final_summary_by_scenario.csv](../../excel_package/final_summary_by_scenario.csv)

Configuration reflected by this table:
- `noise_level`: low, medium, high
- `traffic_pattern`: constant, bursty, intermittent, delayed_start
- `isolation_method`: none, tc, ebpf, adaptive
- `containers`: 3 (in this scenario summary)
- `identity_mode`: mostly ip; adaptive includes cgroup rows
- Metric plotted: `avg_p99_ms`, weighted by `runs`

## Key results

Best method per noise level from aggregated method-by-noise view:
- Low noise: `adaptive` ≈ 1.611 ms
- Medium noise: `tc` ≈ 4.480 ms
- High noise: `tc` ≈ 9.406 ms

Per-scenario winners (noise + traffic):

- Low + constant: `adaptive` ≈ 1.333 ms (about 56.8% lower than `none`)
- Low + bursty: `adaptive` ≈ 2.572 ms (about 10.7% lower than `none`)
- Low + intermittent: `none` ≈ 1.142 ms
- Low + delayed_start: `none` ≈ 1.112 ms

- Medium + constant: `ebpf` ≈ 4.862 ms (about 17.7% lower than `none`)
- Medium + bursty: `none` ≈ 4.306 ms
- Medium + intermittent: `ebpf` ≈ 3.831 ms (about 1.9% lower than `none`)
- Medium + delayed_start: `tc` ≈ 4.548 ms (about 8.3% lower than `none`)

- High + constant: `ebpf` ≈ 10.424 ms (about 3.2% lower than `none`)
- High + bursty: `ebpf` ≈ 8.031 ms (about 1.3% lower than `none`)
- High + intermittent: `tc` ≈ 8.082 ms (about 10.7% lower than `none`)
- High + delayed_start: `ebpf` ≈ 10.395 ms (about 2.0% lower than `none`)

## Conclusion

There is no universal winner. Best method is scenario-dependent:
- `adaptive` is strongest in low-noise constant/bursty cases.
- `ebpf`/`tc` are usually better in medium/high noise scenarios.
- A policy that selects method by `(noise, traffic)` is likely better than a single fixed method.
