# eBPF Noisy Neighbor Publication Platform (runC only)

This repository is a script-driven research platform to evaluate:

**How effectively does an eBPF-based data plane mitigate noisy-neighbor interference under dynamic, real-world conditions?**

## Core design constraints

- runC only (no Docker, Kubernetes, containerd, podman)
- Linux primitives only (`ip`, `tc`, namespaces, cgroups)
- eBPF in C compiled by `clang`
- Works on WSL2, designed for later bare-metal runs
- End-to-end reproducible automation (no manual experiment steps)

## Architecture

- core/: adaptive control and result finalization
  - `core/adaptive_controller.py`
  - `core/bpf_map_ctl.py`
  - `core/finalize_results.py`
- ebpf/: tc programs
  - static: `dropper`, `rate_limit`, `priority`
  - dynamic: `adaptive` (map-driven + cgroup-aware)
- workloads/: victim/noisy/client traffic generators
- analysis/: publication plots
- configs/: reusable matrix configs

## Feature highlights

- Adaptive eBPF control loop (p99 feedback -> dynamic drop-rate updates)
- Cgroup-aware isolation (policy by cgroup ID, not just source IP)
- Isolation method comparison:
  - `none`
  - Linux `tc` baseline (HTB + fq_codel shaping)
  - static eBPF tc
  - adaptive eBPF tc
- Realistic noise patterns:
  - `constant`
  - `bursty`
  - `delayed_start`
  - `intermittent`
- Failure/stress injections:
  - spike
  - churn (noisy container restart)
  - extreme pressure mode
- Statistical rigor:
  - configurable multi-run iterations
  - mean/stddev/95% CI in final summary

## Setup

```bash
cd ebpf-noisy-neighbor
sudo ./environments/base-setup.sh
sudo ./containers/setup-rootfs.sh
```

## Run full research suite

```bash
sudo ./scripts/run-all-experiments.sh ./config.yaml
python3 analysis/plot.py
```

## Reproducible output layout

- raw traces/logs: `results/raw/`
- processed tabular outputs:
  - `results/processed/results.csv`
  - `results/processed/summary.csv`
  - `results/processed/latency_distribution.csv`
- publication package:
  - `results/final/summary.csv`
  - `results/final/results.csv`
  - `results/final/latency_distribution.csv`
  - `results/final/graphs/*.png`
  - `results/final/logs/*.log`
  - `results/final/metadata.json`

## Plot set

- `latency_cdf.png`
- `p99_vs_noise.png`
- `isolation_effectiveness.png`
- `overhead_vs_performance.png`
- `latency_time_series.png`

## Reproducibility notes

- Matrix and iteration policy are config-driven (`config.yaml` or `configs/research_matrix.yaml`)
- Every run records scenario metadata and timestamps
- Finalization computes per-scenario confidence intervals

## Cleanup

```bash
make clean
```
