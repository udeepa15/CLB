# Experiment Guide

This document explains the parameterized experiment matrix and what to compare.

## Topology

- `tenant1` -> HTTP server at `10.0.0.2:8080`
- `tenant2` -> HTTP server at `10.0.0.3:8080`
- `noisy`   -> CPU + network pressure at `10.0.0.4`
- Bridge: `clb-br0` (`10.0.0.1/24`)

## Config-driven matrix

The suite is defined in `config.yaml` and executed by:

```bash
sudo ./scripts/run-all-experiments.sh ./config.yaml
```

Each row defines:
- experiment name
- noise level (`low`, `medium`, `high`)
- eBPF enabled/disabled
- eBPF strategy (`dropper`, `rate_limit`, `priority`)
- container count (`3`, `5`, `10`)
- request count

## Baseline scenario

Script: `experiments/baseline/run.sh` (single-run wrapper)

Steps:
1. Stops any old containers/network state
2. Starts all three runC containers
3. Creates bridge + veth networking
4. Runs client latency tests against both tenants
5. Extracts p99 to `results/processed/summary.csv`
6. Cleans up

Run:

```bash
sudo ./experiments/baseline/run.sh
```

## eBPF-enabled scenario

Script: `experiments/ebpf-enabled/run.sh` (single-run wrapper)

Same flow as baseline, with one extra step:
- Compiles and attaches selected strategy (`dropper`, `rate_limit`, `priority`) on bridge ingress via tc `clsact`

Run:

```bash
sudo ./experiments/ebpf-enabled/run.sh
```

## Comparing results

Main CSV (`results/processed/results.csv`) columns include:
- latency stats (`p50_ms`, `p95_ms`, `p99_ms`)
- jitter (`jitter_ms`)
- throughput (`throughput_rps`)
- packet drops (`packet_drops`)
- isolation score (`isolation_score`)

Plot averages:

```bash
python3 analysis/plot.py
```

Generated graphs:
- `results/processed/p99_vs_noise.png`
- `results/processed/latency_histogram.png`
- `results/processed/baseline_vs_ebpf.png`
- `results/processed/scalability.png`

## Repetition for statistical confidence

Recommended:
- Run each scenario at least 5 times
- Keep `REQUESTS` fixed (example: 500 or 1000)
- Use median or mean of per-run p99 values

Example:

```bash
for i in {1..5}; do REQUESTS=500 sudo ./experiments/baseline/run.sh; done
for i in {1..5}; do REQUESTS=500 sudo ./experiments/ebpf-enabled/run.sh; done
python3 analysis/plot.py
```
