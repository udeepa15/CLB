# p99 by Traffic Pattern and Isolation Method

Chart: [p99_by_traffic_pattern.png](p99_by_traffic_pattern.png)

## What this chart shows

Grouped bars comparing average p99 latency for each traffic pattern across methods.

## Data source and test configuration

Source: [results/excel_package/method_by_traffic_pattern.csv](../../excel_package/method_by_traffic_pattern.csv)

Configuration reflected:
- Traffic patterns: `constant`, `bursty`, `intermittent`, `delayed_start`
- Methods: `none`, `tc`, `ebpf`, `adaptive`
- Metric: `p99_ms_mean`

## Key results

Best method by traffic pattern (lower p99 is better):

- constant: `none` ≈ 3.345 ms
- bursty: `none` ≈ 5.106 ms
- intermittent: `tc` ≈ 4.364 ms (about 7.1% lower than `none` ≈ 4.697)
- delayed_start: `tc` ≈ 5.524 ms (about 0.6% lower than `none` ≈ 5.560)

Other observations:
- `ebpf` is competitive in constant and intermittent, but not best in this aggregate.
- `adaptive` is generally close but above the best method in each traffic class in this file.

## Conclusion

Traffic pattern matters:
- For steady and bursty traffic in this package, `none` has lower observed p99.
- For intermittent and delayed starts, `tc` is slightly better.

A traffic-aware policy (rather than a single fixed method) is likely to produce better tail-latency performance.
