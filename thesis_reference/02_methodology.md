# 2. Methodology

## 2.1 Environment

Recorded automatically in each run's `env.json` (copies: `tables/final_env.json`,
`tables/istio_env.json`).

| Item | Value |
|---|---|
| CPU | Intel Xeon E5-2609 v2, 2.50 GHz, 8 CPUs |
| Memory | 62 GB |
| OS | Ubuntu 22.04.3 LTS, kernel 5.15.0-185-generic, cgroup v2 |
| CPU governor | performance |
| Container runtime | runc 1.3.4 |
| eBPF toolchain | clang 14.0.0, bpftool v5.15.200 |
| Load generators | fortio 1.57.0, hping3 3.0.0-alpha-2 |
| Industry benchmark tools | Envoy 1.31.2, netperf 2.7.0, memcached 1.6.14, memtier_benchmark (built from source) |
| Analysis | Python 3.10.12 with numpy, pandas, matplotlib |

Everything runs on this one machine: tenants, attackers, load generators, controllers and the Envoy
baseline.

## 2.2 CPU configurations

| Configuration | Victims and clients | Attackers | Packet steering | Harness and controller |
|---|---|---|---|---|
| **A, shared CPUs** | CPUs 1–7 | CPUs 1–7 | none | CPU 0 |
| **B, NIC-like** | CPUs 1–5 | CPUs 6–7 | RPS on every tenant port | CPU 0 |

- In A, the attacker's own processes compete with the victims for CPU, and on a veth a local sender
  processes its packets inline on its own CPU.
- In B, attackers have their own CPUs, and receive packet steering (RPS) spreads packet processing
  over the victims' CPUs as a NIC's receive-side scaling would. The interference is then in the
  shared packet path, which is what an early eBPF drop can remove.
- B is the configuration for all main results. A is kept as a negative control.
- Each configuration has its own calibration.

Flags for B: `--tenant-cpus 1-5 --attacker-cpus 6-7 --rps`.

## 2.3 The 100-container experiment (workload 10)

Runner: `workloads/10_scale/run_matrix.py`.

1. The topology and the containers are built once per run.
2. For each trial: attach the arm, start its controller on CPU 0, launch the floods and verify them,
   wait 3 s, then run one fortio client inside every victim's namespace for 30 s.
3. Client *i* calls server *i+1* (a ring), so all victim traffic crosses tenant ports (east–west).
   Each client sends 20 requests/s.
4. Attacker *j* floods victim server *j*. Clients whose server is flooded are *targeted*; the rest
   are *bystanders*.
5. Trials run in a seeded random order.

With 95 clients × 20 requests/s × 30 s, each trial has about 57,000 requests.

### Attack scenarios

| Name | Attackers | hping3 arguments | Offered traffic (measured) |
|---|---|---|---|
| `none` | 0 | – | 0 |
| `a1_flood` | 1 | `--flood -d 1000` | about 150 MB/s |
| `a5_flood` | 5 | `--flood -d 1000` | about 305 MB/s |
| `a5_small` | 5 | `--flood -d 64` | about 34 MB/s at about 349,000 packets/s |
| `a5pct_flood` | max(1, 5 % of N) | `--flood -d 1000` | grows with N (density test) |
| `a5_u1000` … `a5_u10` | 5 | `-i uN -d 1000` | calibration ladder |

### Measurements per trial

- **Pooled victim P50, P90, P99, P99.9:** all clients' latency histograms merged into one mixture.
- Worst-client P99, targeted P99, bystander P99, errors, achieved request rate.
- Tenant ingress packets/s, controller hits/s, attack traffic offered and attack traffic that reached
  the targets (MB/s).
- Busy and softirq CPU fraction on the tenant CPUs.
- Controller tick time, eBPF program run time per packet (`prog_ns_mean`), attach time.
- fortio runs with `-r 0.0001` (0.1 ms histogram buckets). The default 1 ms buckets made
  sub-millisecond P99 differences unmeasurable.

### Validity checks built into the runner

- Every flood is verified to be running before the measurement window. A trial whose floods are not
  all verified (`flood_ok = False`) is excluded by the analysis.
- A smoke test per arm checks map contents and that the attacker is actually dropped.
- A failed trial is recorded and skipped; the run aborts only after three consecutive failures.
- Each arm's cleanup removes leftover qdiscs and pinned maps, so one arm cannot affect the next.

In the runs reported here no trial failed and no trial was excluded.

## 2.4 Density test (1 → 100 containers)

Runner: `workloads/10_scale/run_density.py`.

- The matrix is repeated at N = 1, 10, 20, …, 100 victim containers.
- Each level has max(1, 5 % of N) attackers. N = 1 adds one client-only tenant, because a container
  calling its own IP never leaves its namespace.
- Levels run in a seeded random order, so drift over time cannot line up with N.
- Load per client is fixed at 20 requests/s, so total load grows linearly with N.
- The measurement window is lengthened at small N so that every trial has at least 1,200 requests
  (60 s at N = 1).
- One calibration (made at N = 100) is used for every level. Whether it transfers is part of the
  result.
- 5 arms (`sidecarless`, `qos_open`, `qos_static`, `qos_dynamic_fixed`, `qos_auto_port`) ×
  3 scenarios (`none`, `a1_flood`, `a5pct_flood`) × 5 repetitions × 11 levels = 825 trials.
- Configuration A was run at N = 10, 50, 100 only (225 trials).

## 2.5 Time-varying attacks

Runner: `workloads/10_scale/run_timevarying.py`. 100 containers, configuration B. Every attacker
floods its own victim server and all attackers follow the same schedule in step.

| Scenario | Schedule | What it tests |
|---|---|---|
| `bursty` | 1 s flood, 3 s near-idle, repeated for 40 s | Reaction speed |
| `ramp` | Six 6-second steps of rising rate (`u1000`, `u200`, `u50`, `u20`, `u10`, flood), 36 s | Tracking an escalating attacker |
| `regime` | 30 s *calm* (`u100`, about 9 MB/s per attacker: above the 5 MB/s static cap but near-harmless), then 30 s flood | Efficiency in calm, protection after the switch |

Each phase has its own client window, so victim latency and attacker goodput are measured per phase.
Two metrics are needed because a static cap enforces all the time: on victim latency a controller
can at best tie it. What adaptive control can add is efficiency, letting a heavy tenant through while
it harms nobody. So every phase records victim P99 (protection) and the attacker bytes that reached
the targets (efficiency).

9 arms × 3 scenarios × 10 repetitions = 270 trials.

## 2.6 Industry benchmarks (workload 11)

Runner: `workloads/11_benchmarks/run_bench.py`. 100-tenant testbed, configuration B. These are the
benchmarks that existing service meshes and eBPF data paths publish, with a real Envoy sidecar as an
extra arm on the same machine.

| Benchmark | Replicates | Load | Primary metric |
|---|---|---|---|
| `istio` | Istio's data-plane benchmark | fortio, HTTP/1.1, 1 KB payload, 1000 requests/s, one client → server pair | P99; latency added over `bare` |
| `netperf` | Cilium's CNI benchmark | TCP_RR, TCP_CRR, TCP_STREAM; 1 and 8 flows | P99 transaction latency; throughput |
| `memcached` | Isolation papers (Heracles, Caladan) | memtier_benchmark at fixed rates of 10k–80k operations/s, plus a saturation point | P99 against offered load |

Istio variants:

| Variant | Connections | Request timing |
|---|---|---|
| `c16_q1000` | 16 | fortio's default: all connections fire at the same instant (Istio's classic profile) |
| `c4_q1000` | 4 | default |
| `c16_q1000_uniform` | 16 | `-uniform`: requests spread evenly over time |

Conditions: `idle` (only the measured application) and `noisy` (5 attackers flood servers 1–5, which
include the measured servers).

Design: 8 arms × 2 conditions × 3 variants × 10 repetitions = 480 trials for `istio`, 30 s each, in
one randomized run.

Why the uniform variant exists: on the 16-connection profile Envoy's two workers receive 16 requests
at the same instant and queue them. A probe measured about 0.25 ms through two Envoy hops with one
connection and 0.56 ms with spread-out requests, against 2.5 ms at the median on the synchronized
profile. The synchronized profile is the one Istio publishes, so both are reported.

## 2.7 Statistics

Implemented in `workloads/shared/stats_common.py` and the analysis scripts.

- **Summary:** median with a 95 % bootstrap confidence interval of the median.
- **Test:** two-sided Mann–Whitney U, with tie correction and a continuity-corrected normal
  approximation. With 10 repetitions per arm, complete separation gives p ≈ 0.00017.
- **Effect sizes:** Hodges–Lehmann shift (the median of all pairwise differences, in the metric's
  unit) and Cliff's delta.
- **Multiple comparisons:** Bonferroni, applied within a family of comparisons that was planned
  before looking at the data. Correcting over every metric and every pair (about 1,000 tests) would
  make significance impossible at 10 repetitions, so each experiment has a small primary family.

| Experiment | Primary family | Size | Threshold |
|---|---|---|---|
| Final benchmark | each arm vs `qos_open`, on P99, per scenario | 32 | 0.0016 |
| Time-varying, question 1 | each arm vs `qos_open`, on P99, per phase | 32 | 0.0016 |
| Time-varying, question 2 | each controller vs `qos_static`, on P99 and on goodput | 24 each | 0.0021 |
| Istio benchmark, family A | each arm vs `bare`, on P99 | 42 | 0.0012 |
| Istio benchmark, family B | each of our arms vs `envoy_sidecar`, on P99 | 30 | 0.0017 |

- **Density slopes:** Theil–Sen slope of pooled P99 against N, with a stratified bootstrap confidence
  interval; the difference of each arm's slope from `qos_open` uses a Bonferroni-adjusted interval
  (threshold 0.004), and is significant when the interval excludes zero.
- **Attack damage:** P99 under attack divided by P99 without attack at the same N.
- **Detection quality:** precision and recall of the flagged set against the tenants file, the
  number of falsely flagged tenants, and the time from attack start to classification.

A repetition count of 5 cannot support these tests: the smallest possible p-value is about 0.008,
above every threshold. This is why the 5-repetition netperf and memcached runs are only descriptive.
