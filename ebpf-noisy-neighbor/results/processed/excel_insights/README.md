# Excel Insights Charts — Detailed Explanations

This folder contains interpretation notes for each chart generated from the Excel package dataset.

Charts and explanation files:

1. [p99_heatmap_by_noise_and_traffic.png](p99_heatmap_by_noise_and_traffic.png)
   - [p99_heatmap_by_noise_and_traffic.md](p99_heatmap_by_noise_and_traffic.md)
2. [method_overview_latency_throughput_score.png](method_overview_latency_throughput_score.png)
   - [method_overview_latency_throughput_score.md](method_overview_latency_throughput_score.md)
3. [p99_distribution_boxplot_by_method.png](p99_distribution_boxplot_by_method.png)
   - [p99_distribution_boxplot_by_method.md](p99_distribution_boxplot_by_method.md)
4. [p99_by_failure_mode.png](p99_by_failure_mode.png)
   - [p99_by_failure_mode.md](p99_by_failure_mode.md)
5. [p99_by_traffic_pattern.png](p99_by_traffic_pattern.png)
   - [p99_by_traffic_pattern.md](p99_by_traffic_pattern.md)

## Dataset used

Primary sources:
- [results/excel_package/final_summary_by_scenario.csv](../../excel_package/final_summary_by_scenario.csv)
- [results/excel_package/method_overview.csv](../../excel_package/method_overview.csv)
- [results/excel_package/raw_results_full.csv](../../excel_package/raw_results_full.csv)
- [results/excel_package/method_by_failure_mode.csv](../../excel_package/method_by_failure_mode.csv)
- [results/excel_package/method_by_traffic_pattern.csv](../../excel_package/method_by_traffic_pattern.csv)

## Test configuration context

- Isolation methods: `none`, `tc`, `ebpf`, `adaptive`
- Noise levels: `low`, `medium`, `high`
- Traffic patterns: `constant`, `bursty`, `intermittent`, `delayed_start`
- Failure modes (aggregated views): `none`, `spike`, `churn`
- Containers: predominantly 3 in scenario summaries
- Identity modes: mostly `ip`; adaptive appears with both `ip` and `cgroup` in scenario summary

## Important interpretation note

The dataset is not fully balanced across methods in all aggregates (for example, `none` has many more low-noise samples in some tables). Prefer per-scenario comparisons (noise + traffic cells) over one global average when choosing a method.
