# 4. Limitations, negative results and open work

## 4.1 Negative results to report

These are results, not failures of the method. Each one bounds a claim.

| Finding | Evidence | What it bounds |
|---|---|---|
| Limiters do not help when attackers share CPUs with victims | Configuration A: all arms overlap under the growing attack; limiters slightly worse under one attacker (`03_results.md` section 4) | The mechanism removes packet-path interference only, not CPU contention between processes |
| Byte-rate caps do not stop small-packet floods | `a5_small`: no limiter significant; 22.8 of 34.5 MB/s still delivered under the static cap (section 2) | Caps must also be expressible in packets per second |
| No controller protects better than a static cap | Final benchmark and time-varying question 2 (sections 2 and 5) | The value of adaptive control is efficiency, not protection |
| Controllers polling at 200 ms lag one-second bursts | +1.2 to +2.0 ms against the static cap, significant (section 5) | Reaction time is the cost of adaptivity |
| The DTMC predictor adds nothing at equal polling rate | `qos_markov_200ms` and `qos_dynamic_fixed` are the same in bursts (section 5) | The Markov arm's advantage is its faster polling |
| Forgetting made no measurable difference | `qos_markov_l95` against `qos_markov` (section 5) | The regime-change scenario tested did not need it |
| The automatic detector flags legitimate heavy senders | netperf and memcached controller logs; stream throughput cut to a third (section 6.3) | Detection is by rate alone; "precision 1.0" holds only when legitimate tenants are light |
| IP identity fails under source spoofing | Workload 09 `spoof` (section 6.1) | Identity must be the ingress port |
| `qos_tiered` leaks | 124–208 MB/s delivered against a 5 MB/s cap (section 1) | The interim design is not a working limiter on several CPUs |
| `tc police` protects at least as well as the eBPF limiter | Lowest P99 in every attack scenario (sections 2 and 5) | The eBPF limiter's case is programmability and per-tenant control, not better protection than the kernel policer |
| Protection is small on the Istio profile | Limiters 0.11–0.13 ms below `bare`, not significant on the 16-connection profile (section 8) | The protection claim rests on the many-tenant experiments |
| The eBPF path costs throughput near saturation | memcached peak throughput 5–7 % lower, preliminary (section 9) | "No overhead" holds at moderate load, not at saturation |

## 4.2 Threats to validity

**One machine.** Tenants, attackers, load generators, controllers and the Envoy baseline all run on
one 8-CPU host from 2013. There is no physical NIC in the path; configuration B imitates NIC
receive-side scaling with RPS on veth ports. Absolute latencies would differ on other hardware. The
comparisons between arms are valid because every arm runs under the same conditions.

**Synthetic attack.** The attack is a UDP flood from hping3. It represents a volumetric noisy
neighbour, not an application-level one (retry storms, slow requests, expensive queries).

**Synthetic victims.** In workload 10 every victim sends the same light load, 20 requests/s. Real
tenants are uneven, and section 6.3 shows the automatic arm depends on that evenness.

**Oracle roles.** The static, reactive and Markov arms are told who the attackers are. Only the
automatic arms decide for themselves. A comparison between them is a comparison of "who gets
capped", and the oracle arms are an upper bound on what detection can achieve.

**Calibration.** `H0` and `capacity_pps` are measured on this machine at 100 tenants. They
transferred across densities here, but a new machine or workload needs a new calibration, and the
calibration itself needs an attack sweep with no limiter.

**The `sidecarless` baseline may be a strawman.** It performs 50 updates of one shared map key per
packet (`MESH_UPDATES` = 50). An examiner can fairly ask whether a real sidecarless mesh does that.
The code can be built with 1 or 10 updates, but that sweep has not been run. Until it is, present
`sidecarless` as a worst-case shared-state design, and base the claims on `qos_open` and `bare`.

**The Envoy comparison is of path cost, not features.** Envoy parses HTTP and can encrypt; the eBPF
path does neither. The testbed's Envoy path also has no iptables redirection, which real Istio adds.

**Repetitions.** Density used 5 repetitions per cell; its tests are on slopes across 11 levels
(55 points per arm), not on single cells. The other main experiments used 10.

**Normal approximation.** Mann–Whitney p-values use a normal approximation. At 10 repetitions it is
adequate but not exact; treat p-values close to a threshold as borderline.

**The limiter correctness test was not logged.** See `03_results.md` section 1: rerun and save it.

## 4.3 Numbers that must not be used

- **Every number in the interim report (September 2026).** The limiter then let through 5–22 times
  its cap in the dynamic arms and blocked nearly everything in the static arms, and latency was
  recorded in 1 ms buckets. Table 1 of that report also cannot be traced to any results file.
- **Workloads 01–08.** They have not been rerun on the corrected limiter or with the verified
  launcher.
- **Workload 09 latency.** That run (5 Nov) predates the limiter rewrite (6 Nov). Only its detection
  results (precision, recall, spoofing) are usable.
- **`latency_comparison*.csv` and its plots.** These hold synthetic data.
- **The 5-repetition netperf and memcached tests,** as tested results (section 9).
- **The claim that hits per second measures lock contention.** It counts eBPF program runs. It is a
  packet-rate signal.
- **"Stackelberg equilibrium" as a description of the controller.** The cap is the closed-form Hill
  curve in `01_system_and_arms.md` section 1.5.

## 4.4 Open work

In suggested order:

1. **Finish the netperf and memcached rerun** (10 repetitions, running since 14 Nov) and replace
   section 9 of the results.
2. **Packet-rate caps.** Add a packets-per-second limit beside the byte limit in `qos_limiter.h` and
   rerun `a5_small`. This closes the one attack type with no benefit.
3. **`MESH_UPDATES` sweep** for `sidecarless` (1, 10, 50). No new code; a few hours of machine time.
4. **Decide on `qos_tiered`:** keep it as "the interim design, shown to leak", or drop it from the
   final charts.
5. **The automatic arm's false positives.** Either state it as a limitation, or add a second signal
   (for example, only cap when victims' latency or drops show harm).
6. **Rerun and log `test_limiter.py`.**
7. Optional: faster reaction to bursts (a shorter tick or a kernel-side trigger); multi-hop
   benchmarks (emojivoto with wrk2, DeathStarBench); rerunning workloads 01–08; the same refill bug
   in `test_bed_stall`.
8. **Rewrite the evaluation chapter** around the results in this folder.
