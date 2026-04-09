# p99 by Failure Mode and Isolation Method

Chart: [p99_by_failure_mode.png](p99_by_failure_mode.png)

## What this chart shows

Grouped bars comparing average p99 latency across failure modes for each method.

## Data source and test configuration

Source: [results/excel_package/method_by_failure_mode.csv](../../excel_package/method_by_failure_mode.csv)

Configuration reflected:
- Failure modes in this table: `none`, `spike`, and `churn` (the `churn` rows appear only for `none` in this package)
- Methods: `none`, `tc`, `ebpf`, `adaptive`
- Metric: `p99_ms_mean`

## Key results

For `failure_mode=none`:
- none ≈ 3.292 ms
- tc ≈ 4.320 ms
- ebpf ≈ 4.451 ms
- adaptive ≈ 4.900 ms

For `failure_mode=spike`:
- none ≈ 5.023 ms
- tc ≈ 5.714 ms
- ebpf ≈ 5.813 ms
- adaptive ≈ 6.048 ms

For `failure_mode=churn`:
- only `none` is present (≈ 2.077 ms) in this aggregate table

Interpretation:
- In this package snapshot, `none` is the lowest p99 in available failure-mode aggregates.
- Cross-method comparison for `churn` cannot be made from this file because only `none` exists there.

## Conclusion

The chart indicates lower p99 for `none` under `none`/`spike` failure modes in this dataset. However, for a fair robustness conclusion under churn, additional cross-method churn experiments are needed.
