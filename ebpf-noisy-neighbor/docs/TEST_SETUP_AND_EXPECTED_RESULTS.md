# Test Setup Details and Expected Result Parameters

This document describes how tests are configured in this repository and which output parameters are expected in results.

## 1) Test setup source of truth

Primary configuration:
- [config.yaml](../config.yaml)

Execution guides:
- [docs/EXPERIMENTS.md](EXPERIMENTS.md)
- [docs/GETTING_STARTED.md](GETTING_STARTED.md)

Core matrix dimensions (from config):
- `noise_level`: low, medium, high
- `traffic_pattern`: constant, bursty, delayed_start, intermittent
- `isolation_method`: none, tc, ebpf, adaptive
- `identity_mode`: ip, cgroup
- `container_count`: 3, 5, 10
- `ebpf_strategies`: dropper, rate_limit, priority
- `failure_mode`: none, spike, churn

Global controls:
- `iterations`: 5
- `requests_per_client`: 400
- `p99_threshold_ms`: 8.0
- `sample_interval_s`: 1.0

## 2) Test setup variants used in practice

### A) Full matrix run (recommended)

Command:
- `sudo ./scripts/run-all-experiments.sh ./config.yaml`

Related orchestrator features:
- Supports filters such as `--only-methods`, `--only-noise`, `--only-patterns`, `--only-failures`, `--only-identities`, `--only-iterations`
- Supports `--name-regex`, `--dry-run`, `--skip-finalize`

Purpose:
- Broad coverage across noise, traffic, isolation methods, identity mode, failure injections, and container counts.

Expected output location:
- [results/raw](../results/raw)
- [results/processed](../results/processed)
- [results/final](../results/final)

### B) Single baseline run

Command:
- `sudo ./experiments/baseline/run.sh`

Purpose:
- No-isolation baseline reference for controlled comparisons.

### C) Single eBPF-enabled run

Command:
- `sudo ./experiments/ebpf-enabled/run.sh`

Purpose:
- Quick check of static eBPF method behavior under predefined settings.

### D) Single adaptive/cgroup-aware run

Command example:
- `sudo ./scripts/run-single-experiment.sh adaptive_demo high true 3 adaptive 400 adaptive bursty cgroup 1 none 8.0`

Purpose:
- Validate adaptive closed-loop behavior and cgroup-aware policy updates.

### E) Limited matrix run (smoke/regression subset)

Command:
- `sudo ./scripts/run-limited-experiments.sh`

Purpose:
- Executes a smaller matrix (default `configs/limited_matrix.yaml`) via the same `run-all` pipeline.

### F) Direct single-scenario resource stress run

Command shape:
- `sudo ./scripts/run-single-experiment.sh <name> <noise> <ebpf_enabled> <containers> <strategy> <requests> <method> <pattern> <identity> <iteration> <failure> <p99_threshold_ms> <tenant_cpu_quota_pct> <tenant_memory_mb> <tenant_cpuset> <noisy_cpu_quota_pct> <noisy_memory_mb> <noisy_cpuset> <host_reserved_cpus> <host_mem_pressure_mb> <io_read_bps> <io_write_bps>`

Purpose:
- Explicitly evaluates CPU, memory, cpuset, host pressure, and I/O limits together with isolation behavior.

## 2.1) Script coverage map (from scripts folder)

All test-relevant scripts and what they cover:

- [scripts/run_experiment_matrix.py](../scripts/run_experiment_matrix.py)
  - Expands matrix/scenario definitions into executable experiment rows.
- [scripts/run-all-experiments.sh](../scripts/run-all-experiments.sh)
  - Main orchestrator for full matrix execution and optional filtering.
- [scripts/run-limited-experiments.sh](../scripts/run-limited-experiments.sh)
  - Thin wrapper for limited/smoke matrix execution.
- [scripts/run-single-experiment.sh](../scripts/run-single-experiment.sh)
  - Executes one experiment end-to-end; applies isolation and resource constraints.
- [scripts/start-containers.sh](../scripts/start-containers.sh)
  - Brings up runtime bundles + networking for the current scenario settings.
- [scripts/generate-runtime.sh](../scripts/generate-runtime.sh)
  - Generates container bundles and inventory for variable container counts and noise/pattern/failure context.
- [scripts/stop-containers.sh](../scripts/stop-containers.sh)
  - Teardown/cleanup for containers, networking, and tc/eBPF attachment.
- [scripts/collect-metrics.sh](../scripts/collect-metrics.sh)
  - Collects and writes per-tenant metrics into processed CSV outputs.
- [scripts/export_excel_package.py](../scripts/export_excel_package.py)
  - Builds supervisor-facing rollups (method/noise/failure/traffic summaries, outliers, dictionaries).

Note:
- `collect-metrics.sh` and `export_excel_package.py` are analysis/post-processing stages, but they are part of the overall test pipeline and result interpretation.

## 3) Result parameters you should expect

### Per-sample / per-tenant rows

Typical file:
- [results/processed/results.csv](../results/processed/results.csv)

Important columns:
- Scenario descriptors:
  - `experiment`, `timestamp`, `noise_level`, `traffic_pattern`
  - `isolation_method`, `identity_mode`, `strategy`
  - `containers`, `iteration`, `failure_mode`, `tenant`
- Workload counters:
  - `requests`, `failures`
- Latency metrics:
  - `p50_ms`, `p95_ms`, `p99_ms`
  - `jitter_ms`, `variance_ms2`
  - `tail_amplification` (derived as approximately `p99/p50`)
- Throughput and drops:
  - `throughput_rps`, `packet_drops`
- Recovery behavior:
  - `recovery_window_samples`, `degradation_window_samples`
- Resource controls recorded with each row:
  - `tenant_cpu_quota_pct`, `tenant_memory_mb`, `tenant_cpuset`
  - `noisy_cpu_quota_pct`, `noisy_memory_mb`, `noisy_cpuset`
  - `host_reserved_cpus`, `host_mem_pressure_mb`, `io_read_bps`, `io_write_bps`
- cgroup runtime measurements (when available):
  - `cg_cpu_usage_usec`, `cg_cpu_throttled_usec`
  - `cg_memory_current_bytes`, `cg_memory_events_oom`
  - `cg_io_read_bytes`, `cg_io_write_bytes`
- Composite metric:
  - `isolation_score`

Additional processed file:
- [results/processed/latency_distribution.csv](../results/processed/latency_distribution.csv)
  - Histogram-style bucketed latency counts per `(experiment, tenant)`.

### Aggregated publication outputs

Typical files:
- [results/final/summary.csv](../results/final/summary.csv)
- [results/final/results.csv](../results/final/results.csv)

Expected aggregate parameters:
- `runs`
- `avg_p99_ms`
- `stddev_p99_ms`
- `ci95_p99_ms`

Optional export package artifacts:
- [results/excel_package/raw_results_full.csv](../results/excel_package/raw_results_full.csv)
- [results/excel_package/method_overview.csv](../results/excel_package/method_overview.csv)
- [results/excel_package/method_by_noise.csv](../results/excel_package/method_by_noise.csv)
- [results/excel_package/method_by_failure_mode.csv](../results/excel_package/method_by_failure_mode.csv)
- [results/excel_package/method_by_traffic_pattern.csv](../results/excel_package/method_by_traffic_pattern.csv)
- [results/excel_package/experiment_rollup.csv](../results/excel_package/experiment_rollup.csv)

## 4) Expected trends (high-level)

These are expected directional trends, not hard pass/fail thresholds:

- As `noise_level` increases, `p99_ms` usually increases.
- `spike` and `churn` failure modes usually increase `jitter_ms` and tail metrics.
- Controlled methods (`tc`, `ebpf`, `adaptive`) may trade off throughput for improved tail behavior in some scenarios.
- `adaptive` behavior depends strongly on threshold and tuning (`p99_threshold_ms`, controller response).

## 5) What to validate after each run

Minimum validation checklist:

1. Coverage
   - Confirm all expected matrix combinations were executed (balanced counts per method/scenario).
2. Data integrity
   - No missing required fields in [results/processed/results.csv](../results/processed/results.csv).
3. Statistical sanity
   - Verify `avg_p99_ms`, `stddev_p99_ms`, and `ci95_p99_ms` exist in final summary.
4. Visualization
   - Generate charts via [analysis/plot.py](../analysis/plot.py) and inspect for missing-series artifacts.

## 6) Common interpretation caution

Avoid comparing only global means when class counts are imbalanced across methods. Prefer matched-bucket comparisons by `(noise_level, traffic_pattern, failure_mode, identity_mode, containers)`.
