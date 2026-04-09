# p99 Distribution Boxplot by Method

Chart: [p99_distribution_boxplot_by_method.png](p99_distribution_boxplot_by_method.png)

## What this chart shows

Distribution of per-sample p99 latencies for each method (box = IQR, whiskers = spread, outliers hidden in this rendering).

## Data source and test configuration

Source: [results/excel_package/raw_results_full.csv](../../excel_package/raw_results_full.csv)

Configuration reflected:
- Raw per-tenant/per-run samples
- Methods: `none`, `tc`, `ebpf`, `adaptive`
- Mixed across available noise/traffic/failure combinations

## Key distribution statistics

From the raw p99 samples:

- `none` (n=393): Q1 ≈ 1.497, median ≈ 2.277, Q3 ≈ 4.806, P90 ≈ 9.019
- `tc` (n=104): Q1 ≈ 1.573, median ≈ 4.405, Q3 ≈ 7.854, P90 ≈ 9.776
- `ebpf` (n=104): Q1 ≈ 1.757, median ≈ 4.237, Q3 ≈ 8.168, P90 ≈ 10.572
- `adaptive` (n=96): Q1 ≈ 1.612, median ≈ 4.709, Q3 ≈ 9.067, P90 ≈ 10.361

Interpretation:
- `none` shows the lowest median in this mixed dataset.
- Controlled methods show wider upper tails in aggregate, which is consistent with overhead under some conditions.

## Conclusion

This chart is useful for stability/spread checks, but not enough for final method selection by itself. Use together with per-scenario heatmap and grouped charts to avoid Simpson’s paradox from mixed-condition aggregation.
