# Workload 10 – many-tenant benchmark (100 containers)

Default topology `tenants/scale100.json`: 95 victim tenants (each a runc container running the HTTP
victim server) and 5 attacker tenants on one bridge, 10.0.0.0/16. Generate other sizes with
`python3 tenants.py gen --victims N --attackers M --cpus 1-7 --out tenants/<name>.json`.

## What one trial does

1. The topology and the 95 containers are built **once per run** (about 35 s), not per trial.
2. Per trial: attach the arm (about 4.5 s for 202 tc hooks), start its controller on CPU 0, launch and
   verify the floods, wait 3 s, then run one fortio client **inside every victim's namespace** for 30 s.
   Client *i* calls server *i+1* (a ring), so all victim traffic crosses tenant ports (east-west).
3. Attacker *j* floods victim server *j*. Clients whose server is flooded count as **targeted**,
   all others as **bystanders**.

## Measurements (per trial, `results_summary_raw.csv`)

- Pooled victim P50/P90/P99/P99.9: all clients' histograms merged, about 57,000 requests per trial at
  20 qps × 95 clients × 30 s. fortio runs with `-r 0.0001` (0.1 ms buckets); the old default 1 ms
  buckets made sub-millisecond P99 differences unmeasurable.
- Worst and median client P99, targeted/bystander P99, errors, achieved vs requested QPS.
- Context: tenant ingress packets/s, controller hits/s, offered attack MB/s, bytes reaching the targets,
  per-CPU busy and softirq time (tenant CPUs vs controller CPU), and controller tick time (p50/p99).
- Trials whose floods are not all verified (`flood_ok = False`) are excluded by `analyze.py`.

## Calibration (do this first for a new topology)

The automatic arm's fair-share capacity and the Hill knee H0 depend on the node and on the number of
tenants. With 95 tenants, benign traffic alone is about 24,000 packets/s, so the old hand-picked 10,000
flagged every victim. `run_workload_10.sh calibrate` sweeps the attack rate under `qos_open` and writes
`calibration/<tenants name>.json`. That file holds the knee where victim P99 leaves a 25 % band.
arms.py and the controllers read it automatically.

## Scenarios

| name | attackers | hping3 args |
|---|---|---|
| none | 0 | – |
| a1_flood | 1 | `--flood -d 1000` |
| a5_flood | 5 | `--flood -d 1000` |
| a5_small | 5 | `--flood -d 64` (packet-rate heavy) |
| a5_u1000 … a5_u10 | 5 | `-i uN -d 1000` (calibration ladder) |

## Run

```
sudo bash workloads/run_workload_10.sh calibrate     # once per topology
sudo bash workloads/run_workload_10.sh run 10        # N=10 matrix + analysis
```

## Density test: 1 → 100 containers (`run_density.py`)

The same matrix is repeated at N = 1, 10, 20 … 100 victim containers. Each level gets
max(1, 5 % of N) attackers, and N = 1 adds one client-only tenant: a container calling its
own IP never leaves its namespace. Levels run in a seeded random order, so drift cannot line
up with N. Load per client is fixed at 20 req/s, so total load grows linearly with N. The
window is lengthened for small N so every trial has ≥ 1,200 requests (60 s at N = 1). One
calibration is used for every level, so whether it transfers across densities is part of
the result. The run is resumable: rerun with the same `--out` and finished levels are skipped.

```
sudo python3 run_density.py --reps 5 --tenant-cpus 1-5 --attacker-cpus 6-7 --rps \
     --calibration ../../calibration/scale100_rps.json            # config B (as run)
python3 analyze_density.py results/density_<ts>                    # as the normal user
```

`analyze_density.py` writes these files:

| File | Contents |
|---|---|
| `density_summary.csv` | median + 95 % bootstrap CI per (scenario, arm, N) |
| `density_slopes.csv` | Theil–Sen slope vs N, ms per +10 containers, stratified bootstrap CI |
| `density_slope_vs_open.csv` | P99 slope difference vs `qos_open`, Bonferroni-adjusted |
| `density_damage.csv` | P99(attack) / P99(none) at the same N |
| `density_identification.csv` | auto-arm precision, recall, false-positive tenants vs N |
| `density_setup.csv` | container start-up cost vs N |
| `plots/` | the corresponding charts |

## Configurations

- **A, shared CPUs** (default): victims, clients and floods share CPUs 1–7. The attacker's own
  processes compete with the victims for CPU, and on veth a local sender processes its packets
  inline on its own CPU.
- **B, NIC-like** (`--tenant-cpus 1-5 --attacker-cpus 6-7 --rps`): attackers run on their own
  CPUs, and RPS on every tenant port spreads packet processing over the victims' CPUs, as NIC
  RSS does. This is the interference an early eBPF drop can remove. Calibrate it separately:
  `calibration/scale100_rps.json`.
