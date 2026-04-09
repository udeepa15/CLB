# Run Tests Guide

This guide explains how to run the full test suite and the advanced kernel-space interference tests.

## 0) Environment

Run on Linux/WSL2 Ubuntu with sudo privileges.

From project root:

```bash
cd ebpf-noisy-neighbor
```

Base setup (once per machine):

```bash
sudo ./environments/base-setup.sh
sudo ./containers/setup-rootfs.sh
```

---

## 1) Full matrix tests (recommended)

Run all configured scenarios from `config.yaml`:

```bash
sudo ./scripts/run-all-experiments.sh ./config.yaml
```

Run with filters:

```bash
sudo ./scripts/run-all-experiments.sh ./config.yaml \
  --only-methods adaptive,ebpf \
  --only-noise high \
  --only-patterns bursty,intermittent
```

Dry run (show selected experiments only):

```bash
sudo ./scripts/run-all-experiments.sh ./config.yaml --dry-run
```

---

## 2) Limited matrix (smoke/regression)

```bash
sudo ./scripts/run-limited-experiments.sh
```

Or with explicit config:

```bash
sudo ./scripts/run-limited-experiments.sh ./configs/limited_matrix.yaml
```

---

## 2.1) One-hour all-types suite (recommended quick comprehensive run)

Runs a curated set that covers:
- `none`, `tc`, `ebpf` (`dropper/rate_limit/priority`), `adaptive`
- `ip` and `cgroup`
- `none/spike/churn` failure modes
- multiple noise/pattern combinations
- resource-variation scenarios
- optional advanced tests (XDP vs TC + map pressure)

Command:

```bash
sudo ./scripts/run-one-hour-suite.sh
```

Useful overrides:

```bash
sudo REQUESTS=250 INCLUDE_ADVANCED=true ./scripts/run-one-hour-suite.sh
sudo INCLUDE_ADVANCED=false ./scripts/run-one-hour-suite.sh
```

---

## 3) Single scenario test

Use for debugging one specific case.

```bash
sudo ./scripts/run-single-experiment.sh \
  exp_single_demo high true 3 rate_limit 400 \
  ebpf bursty ip 1 none 8.0 \
  80 512 auto 60 384 auto "" 0 0 0 tc
```

Arguments order:

1. experiment name
2. noise level
3. ebpf enabled (`true|false`)
4. container count
5. strategy (`none|dropper|rate_limit|priority|adaptive`)
6. requests
7. isolation method (`none|tc|ebpf|adaptive`)
8. traffic pattern
9. identity mode (`ip|cgroup`)
10. iteration
11. failure mode (`none|spike|churn|extreme`)
12. p99 threshold ms
13-22. resource controls (tenant/noisy cpu+mem+cpuset, host pressure/io)
23. hook point (`tc|xdp`)

---

## 4) Advanced test: eBPF map pressure (kernel churn)

Runs adaptive+cgroup scenario with high-rate map updates (`100k+` updates/sec target), then measures victim map lookup latency.

```bash
sudo ./experiments/map-pressure/run.sh
```

Optional env overrides:

```bash
sudo TARGET_UPDATES_PER_SEC=120000 MAP_CHURN_DURATION_S=45 REQUESTS=1000 ./experiments/map-pressure/run.sh map_pressure_custom
```

Outputs:

- `results/raw/<experiment>_map_churn_report.csv`
- `results/raw/<experiment>_map_lookup_latency.csv`
- appends into `results/processed/results.csv` and `results/raw/results.csv`

---

## 5) Advanced test: XDP vs TC benchmark

Compares same strategy at different hook points.

```bash
sudo ./experiments/hook-benchmark/run.sh rate_limit
```

or

```bash
sudo ./experiments/hook-benchmark/run.sh dropper
```

Outputs are included in the main results CSV files with `hook_point` column.

---

## 6) Advanced test: policy convergence tracker (control-plane lag)

Measures time from config update to reflected eBPF map state.

Example:

```bash
python3 ./scripts/policy_convergence_tracker.py \
  --config ./config.yaml \
  --set-key global.p99_threshold_ms \
  --set-value 7.5 \
  --map-name control_map \
  --expected-drop-rate 300 \
  --report results/raw/policy_convergence.csv \
  --experiment policy_lag_demo
```

If you have an apply command, pass it with `--apply-command`.

---

## 7) Kernel contention profiling details

`collect-metrics.sh` automatically triggers:

- `scripts/kernel-contention-profiler.sh`

It captures and appends per-tenant kernel-space metrics where available:

- `kernel_ksoftirqd_latency_us`
- `kernel_ebpf_prog_time_ns_per_run`
- related program metadata fields

---

## 8) Generate plots and summaries

Standard plots:

```bash
python3 ./analysis/plot.py
```

Extended Excel-style insight plots:

```bash
python3 ./analysis/plot_excel_insights.py
```

Finalize publication outputs:

```bash
python3 ./core/finalize_results.py --root "$(pwd)" --config ./config.yaml
```

---

## 9) Cleanup

```bash
sudo ./scripts/stop-containers.sh
sudo ./ebpf/tc/attach.sh detach dropper auto
```

---

## 10) Key result fields to verify

In `results/processed/results.csv` (and `results/raw/results.csv`):

- latency percentiles: `p50_ms`, `p95_ms`, `p99_ms`, `p999_ms`, `p9999_ms`
- throughput and drops: `throughput_rps`, `packet_drops`
- map pressure metric: `map_lookup_latency_us`
- kernel contention metrics: `kernel_ksoftirqd_latency_us`, `kernel_ebpf_prog_time_ns_per_run`
- control-plane lag metric: `control_plane_convergence_ms`

If these fields are populated, advanced instrumentation is active.
