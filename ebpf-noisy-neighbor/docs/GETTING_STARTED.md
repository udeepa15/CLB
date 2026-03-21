# Getting Started

This guide is the fastest path to run the project end-to-end.

## 1) Prerequisites

Host: Linux (WSL2 Ubuntu is supported)

Required tools:
- `sudo`
- `runc`
- `iproute2`
- `clang` / `llvm`
- `python3`
- `curl`
- `jq`

Install dependencies and apply kernel/network settings:

```bash
cd ebpf-noisy-neighbor
sudo ./environments/base-setup.sh
```

## 2) Build container rootfs bundles

```bash
sudo ./containers/setup-rootfs.sh
```

This prepares runC bundles in:
- `containers/runtime/tenant1`
- `containers/runtime/tenant2`
- `containers/runtime/noisy`

## 3) Run full config-driven suite

```bash
sudo ./scripts/run-all-experiments.sh ./config.yaml
```

## 4) Optional single baseline experiment (no eBPF)

```bash
sudo ./experiments/baseline/run.sh
```

## 5) Optional single eBPF-enabled experiment

```bash
sudo ./experiments/ebpf-enabled/run.sh
```

## 6) Generate plots

```bash
python3 analysis/plot.py
```

Output files:
- `results/raw/*.latency`
- `results/raw/*.log`
- `results/raw/*.txt`
- `results/processed/summary.csv`
- `results/processed/results.csv`
- `results/processed/p99_vs_noise.png`
- `results/processed/latency_histogram.png`
- `results/processed/baseline_vs_ebpf.png`
- `results/processed/scalability.png`

## 7) Cleanup

```bash
sudo ./scripts/stop-containers.sh
sudo ./ebpf/tc/attach.sh detach
```

---

## One-command Makefile flow

```bash
make setup
make baseline
make ebpf
python3 analysis/plot.py
make clean
```
