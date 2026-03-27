# Method Overview: Latency vs Throughput vs Isolation Score

Chart: [method_overview_latency_throughput_score.png](method_overview_latency_throughput_score.png)

## What this chart shows

- Bars: average p99 latency and average throughput for each isolation method
- Line: isolation score for each method

## Data source and test configuration

Source: [results/excel_package/method_overview.csv](../../excel_package/method_overview.csv)

Configuration reflected by this table:
- Methods: `none`, `tc`, `ebpf`, `adaptive`
- Aggregated across all available noise/traffic/failure combinations in the package
- Sample counts are not equal across methods (important)

## Key results (from table)

- `none`: p99 ≈ 3.671 ms, throughput ≈ 25.179 rps, isolation score ≈ 2.496
- `tc`: p99 ≈ 5.017 ms, throughput ≈ 23.664 rps, isolation score ≈ 3.691
- `ebpf`: p99 ≈ 5.132 ms, throughput ≈ 23.429 rps, isolation score ≈ 3.786
- `adaptive`: p99 ≈ 5.474 ms, throughput ≈ 23.098 rps, isolation score ≈ 4.027

Interpretation:
- Global average p99/throughput appears best for `none`.
- Isolation score is best for `adaptive`, then `ebpf`, then `tc`.
- This signals a trade-off: stronger isolation behavior can increase tail-latency/throughput cost in global averages.

## Conclusion

Use this chart for high-level trade-off direction only. For method selection, combine this with scenario-level charts because the global averages are influenced by imbalance in sample distribution across methods.
