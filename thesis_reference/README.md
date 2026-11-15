# Thesis reference: sidecarless eBPF QoS

Project: *Performance Isolation and Tail-Latency Optimization in Multi-Tenant Sidecarless Service Mesh
Architectures* (UCSC final-year research). This folder collects the design, method, results and
limitations of the work as of 14 November 2026, with the figures and result tables, so the thesis can
be written from one place.

Every number here was copied from an analysis file that is also in `tables/`. If a number in the
thesis matters, check it against the named table before citing it.

## What is in this folder

| File | Contents |
|---|---|
| [01_system_and_arms.md](01_system_and_arms.md) | The system: testbed, data plane, limiter, controllers, automatic detection, and the arms that are compared |
| [02_methodology.md](02_methodology.md) | Environment, CPU configurations, experiment procedures, measurements, statistics |
| [03_results.md](03_results.md) | Results of every finished experiment, with tables and the figure to use for each |
| [04_limitations_and_open_work.md](04_limitations_and_open_work.md) | Negative results, threats to validity, numbers that must not be used, unfinished work |
| [05_data_and_reproduction.md](05_data_and_reproduction.md) | Where the raw data is, and the commands that produced each result |
| `figures/` | 13 charts, numbered in the order they are used in `03_results.md` |
| `tables/` | The analysis text files, summary CSVs, test CSVs, configurations and environment records |

## What is not included

- The 10-repetition rerun of the netperf and memcached benchmarks, which was still running when this
  folder was written. The earlier 5-repetition run of those two benchmarks is summarised in
  `03_results.md` section 9 as preliminary only, because its tests cannot reach significance.
- Workloads 01–08 and every number in the interim report. They were measured with a broken limiter
  and must not be cited (see `04_limitations_and_open_work.md`).

## Headline findings

1. **Enforcing a cap protects victims.** At 100 containers with 5 flooding attackers, every working
   limiter cuts victim P99 from 13.2 ms to 7.0–8.2 ms, and P99.9 from 22.1 ms to 11.2–13.4 ms. With one
   attacker, P99 falls from 6.1 ms to 3.6–4.1 ms. All of these are statistically significant.
2. **The limiters cost nothing measurable when nobody attacks.** Victim P99 without attack is 2.5–2.9 ms
   for every limiter against 2.6 ms with no limiter, and the eBPF program adds 0.01–0.02 ms over a
   plain Linux bridge on the Istio benchmark profile.
3. **The benefit grows with density.** Under an attack that grows with the number of containers, victim
   P99 rises 0.60–0.62 ms per 10 added containers with a limiter and 1.14 ms without.
4. **How the cap is chosen matters less than having one.** A static cap and kernel `tc police` protect
   as well as or better than the reactive, adaptive, Markov and automatic controllers.
5. **What adaptive control buys is efficiency.** When the heavy tenant's traffic is harmless, the
   controllers let all of it through (43.7 MB/s) while a static cap allows 25 MB/s, at no cost to
   victims. The price is 1.2–2.0 ms worse P99 than a static cap during sudden one-second bursts.
6. **Against an Envoy sidecar on the same machine,** the eBPF path is 0.8–3.2 ms lower at P99 when
   idle and 1.3–5.4 ms lower under floods, significant in all 24 comparisons.
7. **The shared-key `sidecarless` design does not scale.** Its P99 rises from 0.8 ms at one container to
   22.8 ms at 100 with no attack at all, and reaches 580–980 ms under attack.

## Findings that limit the claims

- The limiters do nothing when attackers share CPUs with the victims (configuration A).
- Byte-rate caps do not stop floods of small packets.
- The automatic detector flags legitimate tenants that send heavily (bulk TCP streams, a loaded
  memcached), and cut their stream throughput to a third.
- The interim design `qos_tiered` leaks 124–208 MB/s against a 5 MB/s cap.

The detail for each is in `03_results.md` and `04_limitations_and_open_work.md`.
