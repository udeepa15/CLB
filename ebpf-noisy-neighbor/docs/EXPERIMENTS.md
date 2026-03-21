# Experiment Guide

This document explains what each scenario does and what to compare.

## Topology

- `tenant1` -> HTTP server at `10.0.0.2:8080`
- `tenant2` -> HTTP server at `10.0.0.3:8080`
- `noisy`   -> CPU + network pressure at `10.0.0.4`
- Bridge: `clb-br0` (`10.0.0.1/24`)

## Baseline scenario

Script: `experiments/baseline/run.sh`

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

Script: `experiments/ebpf-enabled/run.sh`

Same flow as baseline, with one extra step:
- Compiles and attaches `ebpf/tc/limiter.c` on bridge ingress via tc `clsact`

Run:

```bash
sudo ./experiments/ebpf-enabled/run.sh
```

## Comparing results

Summary CSV columns:
- `timestamp`
- `scenario` (`baseline` or `ebpf`)
- `tenant` (`tenant1`, `tenant2`)
- `requests`
- `failures`
- `p99_ms`

Plot averages:

```bash
python3 analysis/plot.py
```

Generated graph:
- `results/processed/p99_comparison.png`

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
