# eBPF Noisy Neighbor Benchmark (runC)

A reproducible research project to evaluate the noisy-neighbor effect on p99 latency and the impact of tc/eBPF mitigation in a pure runC environment.

## What this repository does

- Starts 3 containers with runC:
  - `tenant1` (latency-sensitive HTTP server)
  - `tenant2` (latency-sensitive HTTP server)
  - `noisy` (CPU + network pressure)
- Creates a dedicated Linux bridge and veth pairs with static IPs:
  - `tenant1`: `10.0.0.2`
  - `tenant2`: `10.0.0.3`
  - `noisy`: `10.0.0.4`
- Runs two scenarios:
  - **baseline** (no eBPF traffic control)
  - **ebpf-enabled** (tc classifier drops noisy source traffic)
- Collects p99 latency and generates comparison plots.

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

## Quick start

```bash
cd ebpf-noisy-neighbor
make setup
make baseline
make ebpf
python3 analysis/plot.py
```

Detailed guides:

- `docs/GETTING_STARTED.md`
- `docs/EXPERIMENTS.md`
- `docs/TROUBLESHOOTING.md`

Generated outputs:

- Raw latency samples: `results/raw/*.latency`
- Per-run logs: `results/raw/*.log`
- Extracted summary: `results/processed/summary.csv`
- Plot image: `results/processed/p99_comparison.png`

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

### 3) Baseline experiment (no eBPF)

```bash
sudo ./experiments/baseline/run.sh
```

### 4) eBPF-enabled experiment

```bash
sudo ./experiments/ebpf-enabled/run.sh
```

### 5) Plot results

```bash
python3 analysis/plot.py
```

---

## Expected output

Typical console summary from `workloads/client.sh`:

```text
SUMMARY target=http://10.0.0.2:8080 requests=300 failures=0 p50_ms=1.1 p90_ms=2.0 p99_ms=5.8 avg_ms=1.4
```

`results/processed/summary.csv` example:

```csv
timestamp,scenario,tenant,requests,failures,p99_ms
2026-03-21T12:00:00+00:00,baseline,tenant1,300,0,18.42
2026-03-21T12:05:00+00:00,ebpf,tenant1,300,0,6.11
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
- For stricter shaping (not just drop), extend `ebpf/tc/limiter.c` with maps and probabilistic/drop policies.
