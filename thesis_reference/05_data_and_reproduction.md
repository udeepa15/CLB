# 5. Data locations and reproduction

All paths are relative to the repository root, `/home/udeepa/CLB`, on the server.

## 5.1 Result folders

The raw result folders are **not in git** (`test_bed_qos/workloads/*/results/` is ignored). They
exist only on the server. Archive them before the server is reused.

| Experiment | Folder under `test_bed_qos/workloads/` | Size |
|---|---|---|
| Final benchmark | `10_scale/results/final_B_N10_20261106_225609/` | 352 MB |
| Density, configuration B | `10_scale/results/density_B_N5_20261106_125230/` | 394 MB |
| Density, configuration A | `10_scale/results/density_A_N5_20261106_225609/` | 125 MB |
| Time-varying | `10_scale/results/timevarying_B_N10_20261107_063258/` | 364 MB |
| Calibration sweep, configuration B | `10_scale/results/sweepB_N3_20261106_110928/` | 19 MB |
| Calibration sweep, configuration A | `10_scale/results/sweep_N3_scale100_20261106_101823/` | 18 MB |
| Automatic detection, small testbed | `09_auto_identification/results/multi_run_N10_20261105_083340/` | 275 MB |
| Istio benchmark | `11_benchmarks/results/istio_B_N10_20261113_183423/` | 29 MB |
| netperf, 5 repetitions (preliminary) | `11_benchmarks/results/netperf_B_N5_20261113_183423/` | 22 MB |
| memcached, 5 repetitions (preliminary) | `11_benchmarks/results/memcached_B_N5_20261113_183423/` | 83 MB |
| netperf and memcached, 10 repetitions | `11_benchmarks/results/netperf_B_N10_20261114_163800/` and a `memcached_B_N10_…` folder | running |

Folders to ignore: `pilot*`, `density_dryrun_*`, `_aborted_istio_noenvoy_*`,
`multi_run_N1_*`, `multi_run_N2_*`, and `09_auto_identification/results/multi_run_N10_20261105_143236`
(a later 30-trial check with other scenarios).

Each result folder holds:

| File | Contents |
|---|---|
| `results_summary_raw.csv` | one row per trial, every measurement |
| `raw/<trial>/` | per-client fortio JSON, controller log (`controller.jsonl`), trial summary |
| `config.json`, `env.json` | the exact run configuration and environment |
| `analysis.txt` | the printed analysis |
| `stats_*.csv`, `density_*.csv`, `tv_*.csv`, `bench_*.csv` | summary and test tables |
| `plots/` | every chart, including variants not copied here |

## 5.2 Files in this folder

### `figures/`

| File | Source plot | Used in |
|---|---|---|
| `fig01_final_p99.png` | final `plots/p99_ms_dots.png` | results 2 |
| `fig02_final_p999.png` | final `plots/p999_ms_dots.png` | results 2 |
| `fig03_density_growing_attack.png` | density B `plots/p99_ms_a5pct_flood_qos_arms.png` | results 3 |
| `fig04_density_one_attacker.png` | density B `plots/p99_ms_a1_flood_qos_arms.png` | results 3 |
| `fig05_density_damage.png` | density B `plots/damage_qos_arms.png` | results 3 |
| `fig06_density_no_attack.png` | density B `plots/p99_ms_none.png` | results 3 |
| `fig07_density_sidecarless.png` | density B `plots/p99_ms_a5pct_flood.png` | results 3 |
| `fig08_density_overheads.png` | density B `plots/overheads_qos_arms.png` | results 7 |
| `fig09_configA_growing_attack.png` | density A `plots/p99_ms_a5pct_flood_qos_arms.png` | results 4 |
| `fig10_timevarying_tradeoff.png` | time-varying `plots/tradeoff_regime.png` | results 5 |
| `fig11_timevarying_cells.png` | time-varying `plots/p99_cells.png` | results 5 |
| `fig12_istio_p99.png` | Istio `plots/istio_primary_no_sidecarless.png` | results 8 |
| `fig13_istio_p99_with_sidecarless.png` | Istio `plots/istio_primary.png` | results 8 |

All charts show medians with 95 % bootstrap confidence intervals. They are PNG at 200 dpi. To
change a chart, edit the analysis script and rerun it on the result folder (section 5.4); do not
edit the image.

The `plots/` folders also contain P99.9 and worst-client versions of the density charts, and
versions with and without `sidecarless`.

### `tables/`

| Prefix | Files |
|---|---|
| `final_` | `analysis.txt`, `stats_summary.csv` (medians, quartiles, intervals), `stats_primary.csv` (the 32 primary tests per metric), `config.json`, `env.json` |
| `densityB_`, `densityA_` | `analysis.txt`, `summary.csv`, `slopes.csv`, `slope_vs_open.csv`, `damage.csv`, `identification.csv`, `setup.csv`, `config.json` |
| `timevarying_` | `analysis.txt`, `summary.csv`, `tests.csv`, `config.json` |
| `istio_` | `analysis.txt`, `summary.csv`, `overhead.csv` (latency added over `bare`), `tests.csv` (families A and B), `config.json`, `env.json` |
| `w09_` | `classification.csv` (detection quality per trial) |
| `calibration_` | `configA.json`, `configB.json` |

## 5.3 Code map

Under `test_bed_qos/`:

| Part | Files |
|---|---|
| Limiter | `qos_limiter.h` |
| eBPF programs | `ebpf_qos_dynamic.c`, `ebpf_qos_auto.c`, `ebpf_qos_tiered.c`, `ebpf_mesh_router.c` |
| Attach and cleanup | `attach_ebpf.sh`, `attach_tc.sh` |
| Arms | `arms.py` |
| Controllers | `qos_controller.py`, `markov_experiments/qos_controller_markov.py`, `qos_controller_auto.py`, `fairshare.py` |
| Map access | `bpfmap.py` |
| Topology and containers | `tenants.py`, `topology.py`, `containers.py`, `tenants/*.json` |
| Calibration | `calibrate.py`, `calibration.py`, `calibration/*.json` |
| Envoy baseline | `envoy_sidecar.py` |
| Tests | `test_limiter.py`, `smoke_test.py`, `test_bpfmap.py`, `test_fairshare.py` |
| Workload 10 | `workloads/10_scale/run_matrix.py`, `run_density.py`, `run_timevarying.py`, `analyze.py`, `analyze_density.py`, `analyze_timevarying.py`, `README.md` |
| Workload 11 | `workloads/11_benchmarks/run_bench.py`, `analyze_bench.py`, `README.md` |
| Shared | `workloads/shared/scale_lib.py`, `stats_common.py`, `classify_metrics.py` |
| Launchers | `workloads/run_workload_09.sh`, `run_workload_10.sh`, `run_workload_11.sh` |

## 5.4 Commands

Runs need root. Analysis must run as the normal user, because root's Python has no numpy or
pandas. Long runs should be detached so they survive an SSH disconnect.

### Limiter correctness

```
cd test_bed_qos
sudo python3 test_limiter.py | tee test_limiter_output.txt
```

### Calibration (once per topology and CPU configuration)

```
cd test_bed_qos
sudo bash workloads/run_workload_10.sh calibrate
```

### Final benchmark (as run)

```
cd test_bed_qos/workloads/10_scale
sudo TENANTS_FILE=tenants/scale100.json QOS_CALIBRATION=/home/udeepa/CLB/test_bed_qos/calibration/scale100_rps.json \
  python3 run_matrix.py --reps 10 \
  --architectures sidecarless qos_open qos_tiered qos_static qos_dynamic_fixed qos_dynamic_adaptive qos_markov qos_auto_port qos_tc_police \
  --scenarios none a1_flood a5_flood a5_small --duration 30 \
  --tenant-cpus 1-5 --attacker-cpus 6-7 --rps --out results/final_B_N10_<timestamp>
python3 analyze.py results/final_B_N10_<timestamp>
```

### Density

```
# configuration B, N = 1, 10, ..., 100
sudo python3 run_density.py --reps 5 --tenant-cpus 1-5 --attacker-cpus 6-7 --rps \
  --calibration /home/udeepa/CLB/test_bed_qos/calibration/scale100_rps.json --out results/density_B_N5_<timestamp>
# configuration A, N = 10, 50, 100
sudo python3 run_density.py --levels 10 50 100 --reps 5 \
  --calibration /home/udeepa/CLB/test_bed_qos/calibration/scale100.json --out results/density_A_N5_<timestamp>
python3 analyze_density.py results/density_<...>
```

### Time-varying attacks

```
sudo TENANTS_FILE=tenants/scale100.json QOS_CALIBRATION=/home/udeepa/CLB/test_bed_qos/calibration/scale100_rps.json \
  python3 run_timevarying.py --reps 10 --tenant-cpus 1-5 --attacker-cpus 6-7 --rps --out results/timevarying_B_N10_<timestamp>
python3 analyze_timevarying.py results/timevarying_B_N10_<timestamp>
```

### Industry benchmarks

```
cd test_bed_qos/workloads
sudo setsid nohup bash run_workload_11.sh > w11.log 2>&1 < /dev/null &        # all three phases
sudo PHASES="istio" setsid nohup bash run_workload_11.sh > w11.log 2>&1 < /dev/null &   # one phase
python3 11_benchmarks/analyze_bench.py 11_benchmarks/results/<run>
```

The launcher runs the analysis itself after each phase. It needs `/usr/local/bin/envoy` and
`memtier_benchmark`.

### Automatic detection on the small testbed

```
cd test_bed_qos/workloads/09_auto_identification
sudo python3 ../../smoke_test.py --arms qos_auto_ip qos_auto_port && sudo bash ../run_workload_09.sh 10
```

## 5.5 Practical notes

- If a results folder is owned by root, run `sudo chown -R udeepa:udeepa results` before analysing.
- Do not run builds, installs or other heavy work on the server during a run; it disturbs the
  latency measurements. Trials that overlapped a build on 13 Nov were discarded for this reason.
- Do not start the Docker daemon on the server. It loads bridge netfilter and iptables rules, which
  change the behaviour of the bridge the testbed measures.
- The server also has namespaces `ns_node1` … `ns_node10` that do not belong to this testbed. The
  teardown removes only `ns_victim*`, `ns_attacker*` and `ns_client*`.
