# eBPF Noisy Neighbor Research Framework (runC)

A reproducible, parameterized experiment framework to evaluate noisy-neighbor interference and tc/eBPF isolation effectiveness using **runC only**.

## Capabilities

- Pure `runC` orchestration (no Docker/Kubernetes)
- Dynamic container scaling (`3`, `5`, `10`, configurable)
- Manual Linux networking with bridge + veth + static IP allocation (`10.0.0.x`)
- Adjustable noisy workload intensity (`low`, `medium`, `high`)
- Multiple tc/eBPF strategies:
  - `dropper`
  - `rate_limit`
  - `priority`
- Config-driven experiment matrix via [config.yaml](config.yaml)
- Automated metrics collection:
  - `p50`, `p95`, `p99`
  - jitter
  - throughput
  - packet drops
  - isolation score

---

## Host requirements

- Linux host (WSL2 Ubuntu supported)
- Root/sudo access
- `runC`, `iproute2`, `clang/llvm`, `python3`, `curl`, `jq`

Install + tune kernel settings:

```bash
cd ebpf-noisy-neighbor
sudo ./environments/base-setup.sh
```

> Note: if running in WSL2, use a recent kernel with eBPF/tc enabled.

---

## Quick start (full suite)

```bash
cd ebpf-noisy-neighbor
make setup
sudo ./scripts/run-all-experiments.sh ./config.yaml
python3 analysis/plot.py
```

Detailed guides:

- `docs/GETTING_STARTED.md`
- `docs/EXPERIMENTS.md`
- `docs/TROUBLESHOOTING.md`

Outputs:

- Raw per-experiment logs and latency traces: `results/raw/`
- Legacy summary: `results/processed/summary.csv`
- Main dataset: `results/processed/results.csv`
- Figures:
  - `results/processed/p99_vs_noise.png`
  - `results/processed/latency_histogram.png`
  - `results/processed/baseline_vs_ebpf.png`
  - `results/processed/scalability.png`

---

## Experiment flow

### 1) Setup rootfs + bundles

```bash
sudo ./containers/setup-rootfs.sh
```

This downloads Alpine minirootfs, installs `python3/curl/iperf3`, and prepares runC bundles under `containers/runtime/`.

### 2) Start containers + network

```bash
sudo ./scripts/start-containers.sh
```

### 3) Manual single baseline run

```bash
sudo NOISE_LEVEL=medium CONTAINERS=3 REQUESTS=300 ./experiments/baseline/run.sh
```

### 4) Manual single eBPF run

```bash
sudo NOISE_LEVEL=high CONTAINERS=3 STRATEGY=dropper REQUESTS=300 ./experiments/ebpf-enabled/run.sh
```

### 5) Plot results

```bash
python3 analysis/plot.py
```

---

## Isolation score

The framework computes:

`Isolation Score = p99_with_noise / p99_baseline_low_noise`

Values are recorded in `results.csv` per tenant.

## Expected output

Typical client summary:

```text
SUMMARY target=http://10.0.0.2:8080 requests=300 failures=0 p50_ms=1.1 p95_ms=2.7 p99_ms=5.8 jitter_ms=0.9 throughput_rps=420.2 avg_ms=1.4
```

`results/processed/results.csv` example:

```csv
timestamp,experiment,noise_level,ebpf_enabled,strategy,containers,tenant,requests,failures,p50_ms,p95_ms,p99_ms,jitter_ms,throughput_rps,packet_drops,isolation_score
2026-03-21T12:00:00+00:00,baseline_low_noise,low,false,none,3,tenant1,300,0,0.82,1.10,1.90,0.21,500.2,0,1.0000
2026-03-21T12:05:00+00:00,ebpf_high_noise_dropper,high,true,dropper,3,tenant1,300,0,1.60,2.80,4.10,0.76,320.4,834,2.1579
```

---

## Cleanup

```bash
make clean
```

This stops runC containers, removes network artifacts, and detaches tc/eBPF hooks.

---

## Notes for reproducibility

- Run each scenario multiple times for statistical confidence.
- Pin CPU cores if needed for stricter control.
- Keep host load stable between runs.
- Experiment definitions are controlled from [config.yaml](config.yaml).
- The suite is idempotent and cleans environment between runs.
