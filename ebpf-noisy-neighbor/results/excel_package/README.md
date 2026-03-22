# Excel Package for Supervisor Review

This folder contains Excel-ready CSV files derived from your latest results dataset.

Detailed column-level documentation:
- [docs/INDEX.md](docs/INDEX.md)

## Files
- raw_results_full.csv: all sample rows from results.csv
- final_summary_by_scenario.csv: scenario-level summary from results/final/summary.csv (if present)
- method_overview.csv: method-level aggregates
- method_by_noise.csv: method x noise aggregates
- method_by_failure_mode.csv: method x failure-mode aggregates
- method_by_traffic_pattern.csv: method x traffic-pattern aggregates
- experiment_rollup.csv: experiment-level rollup
- top_200_worst_p99_cases.csv: worst tail-latency samples
- outlier_cases_p99_ge_8ms.csv: all rows with p99 >= 8 ms
- data_dictionary.csv: field definitions

## Suggested Excel Workbook Tabs
1. Raw_Data
2. Method_Overview
3. Method_By_Noise
4. Method_By_Failure
5. Method_By_Traffic
6. Experiment_Rollup
7. Worst_Cases
8. Outliers_8ms
9. Data_Dictionary
