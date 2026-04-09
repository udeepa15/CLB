# High-Level Test Catalog

This is a high-level map of all test types in this repository.

## 1) End-to-end matrix test

Entry point:
- [scripts/run-all-experiments.sh](../scripts/run-all-experiments.sh)

Config-driven generator:
- [scripts/run_experiment_matrix.py](../scripts/run_experiment_matrix.py)

Run model:
- Expands scenario matrix from [config.yaml](../config.yaml)
- Executes repeated iterations per scenario
- Collects raw and processed outputs

Best use:
- Primary research-quality evaluation and method comparison.

## 2) Single-scenario test

Entry point:
- [scripts/run-single-experiment.sh](../scripts/run-single-experiment.sh)

Run model:
- Executes one explicit scenario with passed parameters.
- Supports all methods, traffic patterns, failure modes, and identity modes.

Best use:
- Debugging, reproducing one case, controller tuning.

## 3) Baseline (no isolation) test

Entry point:
- [experiments/baseline/run.sh](../experiments/baseline/run.sh)

Method characteristics:
- Uses `isolation_method=none`.
- Represents noisy-neighbor impact without control.

Best use:
- Reference line for improvement claims.

## 4) Static eBPF test

Entry points:
- [experiments/ebpf-enabled/run.sh](../experiments/ebpf-enabled/run.sh)
- eBPF programs under [ebpf/tc](../ebpf/tc)

Method characteristics:
- `isolation_method=ebpf`
- Strategy examples: `dropper`, `rate_limit`, `priority`

Best use:
- Compare static kernel-level filtering strategies.

## 5) Linux tc baseline shaping test

Entry point:
- [scripts/run-single-experiment.sh](../scripts/run-single-experiment.sh) with `isolation_method=tc`

Method characteristics:
- Linux `tc` shaping baseline (HTB + fq_codel in run path).

Best use:
- Compare against eBPF and adaptive approaches using conventional Linux traffic control.

## 6) Adaptive eBPF closed-loop test

Core components:
- [ebpf/tc/adaptive.c](../ebpf/tc/adaptive.c)
- [core/adaptive_controller.py](../core/adaptive_controller.py)
- [core/bpf_map_ctl.py](../core/bpf_map_ctl.py)

Method characteristics:
- `isolation_method=adaptive`
- Controller updates drop-rate based on observed p99.
- Supports `identity_mode=ip` and `identity_mode=cgroup`.

Best use:
- Evaluate dynamic control behavior and responsiveness under varying stress.

## 7) Failure/stress injection tests

Integrated through single/matrix runs:
- `failure_mode=none`
- `failure_mode=spike`
- `failure_mode=churn`

Best use:
- Robustness and recovery behavior evaluation.

## 8) Post-processing and analytics tests

Plotting and summaries:
- [analysis/plot.py](../analysis/plot.py)
- [analysis/plot_excel_insights.py](../analysis/plot_excel_insights.py)

Graph interpretation reference:
- [docs/GRAPH_EXPLANATIONS.md](GRAPH_EXPLANATIONS.md)

Best use:
- Transform raw experiment data into interpretable visual and statistical outputs.

## 9) Expected output artifacts by test family

Raw artifacts:
- [results/raw](../results/raw)

Processed artifacts:
- [results/processed](../results/processed)
- [results/processed/excel_insights](../results/processed/excel_insights)

Final publication artifacts:
- [results/final](../results/final)

## 10) Recommended execution order (high-level)

1. Environment and runtime setup
   - [docs/ENVIRONMENT_SETUP.md](ENVIRONMENT_SETUP.md)
2. Full matrix execution
3. Plot generation and summary checks
4. Scenario-level interpretation (not only global means)
5. Final report generation

## 11) Minimal acceptance checks

- All intended scenario combinations executed (balanced by method where comparison is required).
- No missing key metrics (`p50_ms`, `p95_ms`, `p99_ms`, `throughput_rps`, `tail_amplification`).
- Final summary includes dispersion and confidence (`stddev_p99_ms`, `ci95_p99_ms`).
