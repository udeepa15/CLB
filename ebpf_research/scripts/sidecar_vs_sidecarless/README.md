# Sidecar vs Sidecarless Test Scripts

This folder contains a dedicated test flow for comparing **sidecar** vs **sidecarless** only.

## Files

- `run_noise_sweep.sh`
  - Runs only these configs:
    - `sidecar_isolation` (Sidecar)
    - `sidecarless_contention` (Sidecarless)
  - Sweeps increasing attacker noise (`noise_target_rps`).
  - Writes metrics to:
    - `ebpf_research/results/sidecar_vs_sidecarless_metrics.csv`

- `plot_latency_vs_noise.py`
  - Reads `sidecar_vs_sidecarless_metrics.csv`
  - Creates exactly 3 graphs:
    - `p50_ms_vs_noise.png`
    - `p95_ms_vs_noise.png`
    - `p99_ms_vs_noise.png`
  - Output folder:
    - `ebpf_research/results/graphs/sidecar_vs_sidecarless/`

## Run

From repository root, execute benchmark sweep as root:

```bash
sudo ./ebpf_research/scripts/sidecar_vs_sidecarless/run_noise_sweep.sh
```

Optional parameters:

```bash
sudo REPEATS=5 NOISE_LEVELS_CSV=100,300,500,700,900,1100 ./ebpf_research/scripts/sidecar_vs_sidecarless/run_noise_sweep.sh
```

By default, attacker noise uses `fortio` (more stable in this setup).
Use `wrk2` only if you explicitly want it:

```bash
sudo ATTACKER_LOAD_TOOL=wrk2 ./ebpf_research/scripts/sidecar_vs_sidecarless/run_noise_sweep.sh
```

Generate the three graphs:

```bash
python ./ebpf_research/scripts/sidecar_vs_sidecarless/plot_latency_vs_noise.py
```

If your CSV path is custom:

```bash
python ./ebpf_research/scripts/sidecar_vs_sidecarless/plot_latency_vs_noise.py \
  --input ebpf_research/results/sidecar_vs_sidecarless_metrics.csv \
  --output-dir ebpf_research/results/graphs/sidecar_vs_sidecarless
```
