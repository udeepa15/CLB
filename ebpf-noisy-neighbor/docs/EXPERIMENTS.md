# Experiment Guide (Publication Workflow)

## Research matrix dimensions

Configured in [config.yaml](../config.yaml) or [configs/research_matrix.yaml](../configs/research_matrix.yaml):

- `noise_level`: `low`, `medium`, `high`
- `traffic_pattern`: `constant`, `bursty`, `delayed_start`, `intermittent`
- `isolation_method`: `none`, `tc`, `ebpf`, `adaptive`
- `identity_mode`: `ip`, `cgroup`
- `container_count`: `3`, `5`, `10`
- `failure_mode`: `none`, `spike`, `churn`
- iterations: default `5` runs per scenario

## Execution

```bash
sudo ./scripts/run-all-experiments.sh ./config.yaml
python3 analysis/plot.py
```

## Adaptive contribution

`isolation_method=adaptive` enables:

1. tc attach of `ebpf/tc/adaptive.c`
2. userspace feedback loop in `core/adaptive_controller.py`
3. dynamic updates through map writes (`core/bpf_map_ctl.py`)

Control law:

- if measured p99 exceeds threshold, increase drop-rate
- otherwise, gradually relax throttling

## Cgroup-aware policy

`identity_mode=cgroup` maps policy using cgroup IDs from `containers/runtime/cgroup_ids.csv`.

`identity_mode=ip` uses noisy source IP matching.

## Baselines for comparison

- `none`: no isolation
- `tc`: Linux HTB + fq_codel shaping baseline
- `ebpf`: static tc/eBPF strategies (`dropper`, `rate_limit`, `priority`)
- `adaptive`: map-driven closed-loop eBPF

## Metrics captured

- percentile latency: `p50/p95/p99`
- full latency distribution buckets: `results/processed/latency_distribution.csv`
- jitter and variance: `jitter_ms`, `variance_ms2`
- tail amplification: `p99/p50`
- throughput and packet drops
- degradation/recovery windows under stress
- isolation score vs low-noise no-isolation baseline

## Statistical outputs

Final summary in `results/final/summary.csv` reports:

- runs
- average p99
- standard deviation
- 95% confidence interval

## Publication-ready outputs

- `results/final/summary.csv`
- `results/final/results.csv`
- `results/final/latency_distribution.csv`
- `results/final/graphs/*.png`
- `results/final/logs/*.log`
- `results/final/metadata.json`
