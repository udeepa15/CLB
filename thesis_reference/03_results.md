# 3. Results

All results are at 100 containers in configuration B unless stated. Values are medians over
repetitions. "Significant" means significant after the Bonferroni correction described in
`02_methodology.md` section 2.7. An asterisk in a table marks a significant difference.

| Section | Experiment | Trials | Dates |
|---|---|---|---|
| 1 | Limiter correctness | – | 6 Nov 2026 |
| 2 | Final benchmark, constant attacks | 360 | 6–7 Nov |
| 3 | Density, 1 → 100 containers | 825 | 6 Nov |
| 4 | Negative control, configuration A | 225 | 6–7 Nov |
| 5 | Time-varying attacks | 270 | 7 Nov |
| 6 | Automatic detection | 200 + the runs above | 5–14 Nov |
| 7 | Overheads | from sections 2 and 3 | – |
| 8 | Istio benchmark against Envoy | 480 | 13 Nov |
| 9 | netperf and memcached (preliminary) | 600 | 13–14 Nov |

No trial failed or was excluded in any of these runs.

---

## 1. The limiter delivers its cap

`test_limiter.py` floods a capped attacker and measures the bytes that reach the victim. On 6 Nov it
delivered 1.00× the cap in every case: one flood, four floods pinned to four CPUs, the cap rewritten
every 20 ms during the flood, and a cap keyed on the port. The test passes when delivery is within
0.85–1.10× the cap at an offered load of at least 3× the cap.

The output of that run was not saved. **Rerun `sudo python3 test_limiter.py` and keep the output
before quoting exact figures.**

The final benchmark confirms it independently. With a 5 MB/s cap per attacker:

| Arm | 1 attacker: delivered | 5 attackers: delivered | Offered |
|---|---|---|---|
| `qos_static` | 5.02 MB/s | 25.1 MB/s | 152 / 307 MB/s |
| `qos_tc_police` | 5.09 MB/s | 25.5 MB/s | 152 / 306 MB/s |
| `qos_tiered` (interim design) | 123.6 MB/s | 207.8 MB/s | 150 / 304 MB/s |
| `qos_open` (no limit) | 149.0 MB/s | 301.2 MB/s | 149 / 301 MB/s |

So the new limiter matches the kernel's own policer to within 2 %, and the interim design does not
enforce its cap when packets are processed on several CPUs.

Source: `tables/final_analysis.txt`, section "context (medians)".

---

## 2. Final benchmark: 100 containers, constant attacks

9 arms × 4 scenarios × 10 repetitions = 360 trials, about 57,000 requests per trial, 0 errors.

**Figures:** `figures/fig01_final_p99.png`, `figures/fig02_final_p999.png`. `sidecarless` is left
out of both charts because it would flatten every other arm.

### Victim P99 (ms), with 95 % confidence interval

| Arm | No attack | 1 flood | 5 floods | 5 small-packet floods |
|---|---|---|---|---|
| `qos_open` (no limit) | 2.62 (2.48–2.79) | 6.07 (5.60–6.55) | 13.19 (12.13–14.76) | 12.94 (11.48–13.63) |
| `qos_static` | 2.60 (2.42–2.89) | 3.62 (3.52–3.83) * | 7.72 (7.50–7.85) * | 12.03 (11.40–12.49) |
| `qos_tc_police` | 2.48 (2.45–2.67) | 3.58 (3.35–3.90) * | 6.96 (6.73–7.55) * | 10.76 (9.61–11.15) |
| `qos_dynamic_fixed` | 2.50 (2.39–2.77) | 3.67 (3.41–3.96) * | 8.16 (7.76–8.51) * | 11.46 (10.96–12.76) |
| `qos_dynamic_adaptive` | 2.88 (2.58–3.27) | 3.82 (3.49–4.23) * | 7.86 (7.40–8.75) * | 11.72 (11.12–12.14) |
| `qos_markov` | 2.51 (2.39–2.68) | 3.86 (3.50–4.32) * | 7.89 (7.56–8.53) * | 10.82 (10.21–11.44) |
| `qos_auto_port` | 2.68 (2.50–2.85) | 4.09 (3.84–4.58) * | 7.96 (7.68–8.91) * | 11.32 (10.68–12.31) |
| `qos_tiered` | 2.74 (2.43–2.96) | 4.61 (4.30–5.18) * | 11.94 (11.20–12.98) | 13.43 (12.47–14.20) |
| `sidecarless` | 13.37 (12.00–14.31) * | 704 (692–728) * | 579 (561–595) * | 981 (968–995) * |

### Shift against `qos_open` (Hodges–Lehmann, ms; family of 32, threshold 0.0016)

| Arm | P99: 1 flood | P99: 5 floods | P99: 5 small | P99: none | P99.9: 1 flood | P99.9: 5 floods |
|---|---|---|---|---|---|---|
| `qos_static` | −2.42 * | −5.46 * | −0.83 | −0.00 | −4.22 * | −10.01 * |
| `qos_tc_police` | −2.49 * | −6.19 * | −2.15 | −0.08 | −4.45 * | −10.95 * |
| `qos_dynamic_fixed` | −2.36 * | −5.12 * | −1.08 | −0.07 | −3.72 * | −9.19 * |
| `qos_dynamic_adaptive` | −2.24 * | −5.36 * | −0.97 | +0.26 | −3.72 * | −9.70 * |
| `qos_markov` | −2.21 * | −5.20 * | −1.80 | −0.10 | −3.68 * | −9.59 * |
| `qos_auto_port` | −1.91 * | −5.19 * | −1.24 | +0.02 | −3.17 * | −9.22 * |
| `qos_tiered` | −1.42 * | −1.22 | +0.55 | +0.06 | −2.05 * | −1.94 |
| `sidecarless` | +698 * | +566 * | +968 * | +10.7 * | +1060 * | +741 * |

### Victim P99.9 (ms)

| Arm | No attack | 1 flood | 5 floods | 5 small-packet floods |
|---|---|---|---|---|
| `qos_open` | 3.96 | 10.16 | 22.14 | 22.21 |
| `qos_static` | 4.07 | 5.91 | 12.21 | 20.38 |
| `qos_tc_police` | 3.83 | 5.67 | 11.23 | 17.77 |
| `qos_dynamic_fixed` | 3.86 | 6.42 | 13.36 | 18.30 |
| `qos_dynamic_adaptive` | 4.43 | 6.34 | 12.85 | 18.85 |
| `qos_markov` | 3.75 | 6.41 | 12.74 | 18.31 |
| `qos_auto_port` | 4.05 | 6.97 | 13.10 | 19.41 |
| `qos_tiered` | 4.03 | 8.05 | 20.25 | 24.55 |
| `sidecarless` | 21.09 | 1069 | 763 | 1104 |

### Why it works: attack traffic and tenant CPU under 5 floods

| Arm | Attack traffic reaching targets (MB/s) | Tenant CPUs busy | Tenant CPUs in softirq |
|---|---|---|---|
| `qos_open` | 301.2 | 0.79 | 0.34 |
| `qos_static` | 25.1 | 0.66 | 0.21 |
| `qos_tc_police` | 25.5 | 0.63 | 0.19 |
| `qos_dynamic_fixed` | 35.0 | 0.66 | 0.22 |
| `qos_dynamic_adaptive` | 35.3 | 0.66 | 0.22 |
| `qos_markov` | 34.9 | 0.66 | 0.21 |
| `qos_auto_port` | 35.0 | 0.67 | 0.22 |
| `qos_tiered` | 207.8 | 0.76 | 0.31 |

Dropping the flood at the attacker's port removes about a third of the packet-processing (softirq)
time on the victims' CPUs. Without attack every arm is at 0.51 busy.

### What to say

- Every working limiter significantly lowers victim P99 and P99.9 under byte-heavy floods: about
  2 ms at P99 under one flood and 5–6 ms under five; about 4 ms and 9–11 ms at P99.9.
- No limiter is significantly different from `qos_open` without attack: limiting costs nothing
  measurable.
- No limiter is significant against floods of 64-byte packets. The caps are in bytes: a 5 MB/s cap
  still admits roughly 47,000 such packets per second per attacker, so the packet rate, which is what
  hurts, is barely reduced (22.8 of 34.5 MB/s still reaches the targets under `qos_static`).
- The controllers are not better than the static cap or `tc police` at protecting. `tc police` has
  the lowest P99 in every attack scenario.
- `qos_tiered` leaks (section 1) and is not significant under five floods.
- `sidecarless` is 10.7 ms worse than `qos_open` with no attack and several hundred ms worse under
  attack.

Sources: `tables/final_stats_summary.csv`, `tables/final_stats_primary.csv`,
`tables/final_analysis.txt`.

---

## 3. Density: 1 → 100 containers

825 trials. One calibration, made at 100 containers, is used at every level.

**Figures:**
- `figures/fig03_density_growing_attack.png`: P99 against N under the growing attack.
- `figures/fig04_density_one_attacker.png`: the same with one constant attacker.
- `figures/fig05_density_damage.png`: attack damage against N.
- `figures/fig06_density_no_attack.png`: no attack, including `sidecarless`.
- `figures/fig07_density_sidecarless.png`: growing attack, including `sidecarless`.

### Pooled victim P99 (ms) by number of containers

| Scenario | Arm | 1 | 10 | 20 | 30 | 40 | 50 | 60 | 70 | 80 | 90 | 100 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Growing attack | `qos_open` | 0.88 | 1.21 | 1.19 | 1.74 | 2.46 | 3.22 | 4.03 | 6.56 | 9.42 | 11.88 | 14.12 |
| | `qos_static` | 0.79 | 1.06 | 1.18 | 1.38 | 1.88 | 2.21 | 2.81 | 3.68 | 5.34 | 6.63 | 8.30 |
| | `qos_dynamic_fixed` | 0.80 | 1.07 | 1.19 | 1.42 | 1.82 | 2.41 | 2.64 | 3.37 | 5.30 | 6.92 | 8.61 |
| | `qos_auto_port` | 0.80 | 1.05 | 1.17 | 1.55 | 1.97 | 2.43 | 2.87 | 3.44 | 6.65 | 7.51 | 9.79 |
| | `sidecarless` | 49.1 | 76.9 | 151 | 194 | 305 | 363 | 398 | 455 | 491 | 537 | 616 |
| One attacker | `qos_open` | 0.88 | 1.11 | 1.29 | 1.54 | 1.80 | 2.11 | 2.66 | 2.87 | 3.18 | 4.20 | 6.42 |
| | `qos_static` | 0.79 | 1.05 | 1.09 | 1.40 | 1.56 | 1.78 | 2.43 | 2.35 | 2.62 | 3.24 | 4.55 |
| | `qos_dynamic_fixed` | 0.80 | 1.08 | 1.19 | 1.46 | 1.56 | 2.03 | 2.16 | 2.43 | 2.54 | 3.51 | 4.56 |
| | `qos_auto_port` | 0.80 | 1.07 | 1.19 | 1.40 | 1.60 | 2.05 | 2.29 | 2.76 | 2.74 | 3.74 | 5.21 |
| | `sidecarless` | 51.8 | 77.3 | 147 | 196 | 238 | 298 | 369 | 466 | 550 | 642 | 774 |
| No attack | `qos_open` | 0.70 | 0.89 | 0.90 | 1.20 | 1.29 | 1.51 | 1.61 | 2.10 | 2.22 | 2.54 | 2.93 |
| | `qos_static` | 0.70 | 0.90 | 0.89 | 1.19 | 1.33 | 1.58 | 1.71 | 1.80 | 2.18 | 2.32 | 2.55 |
| | `qos_dynamic_fixed` | 0.70 | 0.89 | 0.91 | 1.33 | 1.35 | 1.54 | 1.70 | 1.94 | 2.42 | 2.22 | 2.90 |
| | `qos_auto_port` | 0.70 | 0.92 | 0.91 | 1.19 | 1.43 | 1.63 | 1.68 | 1.79 | 2.37 | 2.59 | 2.69 |
| | `sidecarless` | 0.80 | 0.99 | 1.15 | 1.68 | 1.91 | 2.68 | 2.65 | 3.77 | 5.66 | 9.75 | 22.83 |

### Slope of P99 against N (ms per +10 containers, Theil–Sen, 95 % interval)

| Arm | Growing attack | One attacker | No attack |
|---|---|---|---|
| `qos_open` | 1.143 (0.988–1.232) | 0.345 (0.319–0.393) | 0.207 (0.196–0.214) |
| `qos_static` | 0.616 (0.580–0.660) * | 0.279 (0.261–0.300) * | 0.184 (0.177–0.195) * |
| `qos_dynamic_fixed` | 0.601 (0.558–0.642) * | 0.289 (0.263–0.308) * | 0.188 (0.176–0.211) |
| `qos_auto_port` | 0.606 (0.541–0.722) * | 0.304 (0.288–0.321) | 0.200 (0.187–0.212) |
| `sidecarless` | 57.4 (55.7–58.7) * | 68.5 (66.4–70.5) * | 0.700 (0.615–0.791) * |

The asterisk marks a slope significantly different from `qos_open` (Bonferroni-adjusted interval of
the difference excludes zero; threshold 0.004).

### Attack damage: P99 under the growing attack ÷ P99 without attack

| Arm | 10 | 30 | 50 | 70 | 90 | 100 |
|---|---|---|---|---|---|---|
| `qos_open` | 1.36 | 1.45 | 2.14 | 3.13 | 4.68 | 4.82 |
| `qos_static` | 1.18 | 1.16 | 1.40 | 2.05 | 2.87 | 3.25 |
| `qos_dynamic_fixed` | 1.20 | 1.07 | 1.56 | 1.73 | 3.11 | 2.97 |
| `qos_auto_port` | 1.13 | 1.30 | 1.49 | 1.92 | 2.90 | 3.63 |

### What to say

- Under an attack that grows with density, the limiters roughly halve the rate at which victim
  latency rises: 0.60–0.62 against 1.14 ms per 10 added containers. The gap opens from about
  40 containers. At 100 containers P99 is 8.3–9.8 ms with a limiter and 14.1 ms without.
- With one constant attacker the effect is smaller: the static and reactive caps grow significantly
  slower than no limit, the automatic arm does not reach significance.
- Without attack all per-tenant designs grow at about 0.2 ms per 10 containers. The limiters add no
  cost. (`qos_static` is marginally *lower* than `qos_open` here, by 0.02 ms per 10 containers; this
  is too small to interpret.)
- At 100 containers the growing attack multiplies victim P99 by 4.8 with no limit and by 3.0–3.6
  with one.
- `sidecarless` degrades with density even with no attacker (0.8 → 22.8 ms) and collapses under
  attack at every density (49 ms at one container).
- A calibration made at 100 containers worked at every density (see section 6).

Sources: `tables/densityB_*.csv`, `tables/densityB_analysis.txt`.

---

## 4. Negative control: configuration A (shared CPUs)

225 trials at N = 10, 50 and 100, with attackers on the same CPUs as the victims.

**Figure:** `figures/fig09_configA_growing_attack.png`.

| Scenario | Arm | N = 10 | N = 50 | N = 100 | Slope per +10 |
|---|---|---|---|---|---|
| Growing attack | `qos_open` | 0.72 | 1.46 | 8.27 | 0.837 |
| | `qos_static` | 0.75 | 1.53 | 8.60 | 0.872 |
| | `qos_dynamic_fixed` | 0.75 | 1.47 | 8.31 | 0.839 |
| | `qos_auto_port` | 0.77 | 1.53 | 8.89 | 0.903 |
| One attacker | `qos_open` | 0.77 | 1.14 | 1.92 | 0.129 |
| | `qos_static` | 0.76 | 1.16 | 2.16 | 0.151 * |
| | `qos_dynamic_fixed` | 0.75 | 1.18 | 2.25 | 0.162 * |
| | `qos_auto_port` | 0.78 | 1.11 | 2.12 | 0.147 * |
| No attack | `qos_open` | 0.70 | 1.07 | 1.67 | 0.098 |
| | limiters | 0.72–0.74 | 1.05–1.06 | 1.60–1.62 | 0.095–0.100 |

### What to say

- When the attacker shares CPUs with the victims, no limiter helps under the growing attack: all
  arms overlap and no slope differs from `qos_open`.
- Under one attacker the limiters are slightly *worse* than no limit (slopes 0.02–0.03 ms per
  10 containers higher, significant; 2.1–2.3 against 1.9 ms at 100 containers).
- The interference in this configuration is the attacker's own processes taking CPU time from the
  victims. Dropping the attacker's packets cannot return that time.
- This bounds the claim: eBPF rate limiting removes interference in the shared packet path, not CPU
  contention between processes. CPU isolation needs a different mechanism (cgroup CPU limits or
  dedicated cores).
- In this configuration `sidecarless` reaches about 700 ms at 100 containers even with no attack.

Sources: `tables/densityA_*.csv`, `tables/densityA_analysis.txt`.

---

## 5. Time-varying attacks: protection against efficiency

270 trials, about 62,700 requests per phase, 0 errors.

**Figures:** `figures/fig10_timevarying_tradeoff.png` (the trade-off, the key chart of this
section), `figures/fig11_timevarying_cells.png` (P99 per arm in each phase).

### Victim P99 (ms)

| Arm | Bursty | Ramp | Regime: calm | Regime: attack |
|---|---|---|---|---|
| `qos_open` | 9.64 | 8.79 | 3.46 | 12.03 |
| `qos_static` | 5.71 | 5.59 | 3.27 | 7.63 |
| `qos_tc_police` | 4.64 | 4.93 | 2.92 | 6.84 |
| `qos_dynamic_fixed` | 6.95 | 6.10 | 3.68 | 8.26 |
| `qos_dynamic_adaptive` | 6.61 | 5.84 | 3.34 | 7.66 |
| `qos_markov` | 5.99 | 5.51 | 3.37 | 7.58 |
| `qos_markov_200ms` | 7.01 | 5.75 | 3.65 | 8.14 |
| `qos_markov_l95` | 6.17 | 5.93 | 3.66 | 8.10 |
| `qos_auto_port` | 7.54 | 6.00 | 3.64 | 8.87 |

### Attacker traffic delivered to targets (MB/s); offered in brackets

| Arm | Bursty (77) | Ramp (107) | Regime: calm (44) | Regime: attack (305) |
|---|---|---|---|---|
| `qos_open` | 75.6 | 107.1 | 43.7 | 299.6 |
| `qos_static` | 10.2 | 21.5 | 25.1 | 25.1 |
| `qos_tc_police` | 10.3 | 21.7 | 25.5 | 25.5 |
| `qos_dynamic_fixed` | 19.9 | 47.3 | 43.7 | 36.2 |
| `qos_dynamic_adaptive` | 19.9 | 47.6 | 43.7 | 36.3 |
| `qos_markov` | 16.5 | 31.3 | 43.7 | 35.6 |
| `qos_markov_200ms` | 19.7 | 31.9 | 43.7 | 36.1 |
| `qos_markov_l95` | 16.2 | 31.2 | 43.6 | 35.5 |
| `qos_auto_port` | 34.5 | 47.3 | 43.6 | 38.5 |

### Question 1: does limiting help? P99 shift against `qos_open` (ms; family 32)

| Arm | Bursty | Ramp | Regime: attack | Regime: calm |
|---|---|---|---|---|
| `qos_static` | −3.89 * | −2.89 * | −4.33 * | −0.17 |
| `qos_tc_police` | −4.88 * | −3.77 * | −5.18 * | −0.45 |
| `qos_dynamic_fixed` | −2.60 * | −2.67 * | −3.66 * | +0.31 |
| `qos_dynamic_adaptive` | −2.96 * | −2.69 * | −4.13 * | −0.07 |
| `qos_markov` | −3.43 * | −3.06 * | −4.17 * | +0.09 |
| `qos_markov_200ms` | −2.62 * | −2.77 * | −3.89 * | +0.26 |
| `qos_markov_l95` | −3.47 * | −2.76 * | −3.97 * | +0.22 |
| `qos_auto_port` | −1.99 * | −2.52 * | −3.36 * | +0.20 |

### Question 2: does a controller beat the static cap? Shift against `qos_static` (family 24 each)

| Arm | P99, bursty (ms) | P99, ramp | P99, regime attack | P99, regime calm | Goodput, regime calm (MB/s) |
|---|---|---|---|---|---|
| `qos_dynamic_fixed` | +1.28 * | +0.55 | +0.69 | +0.48 | +18.5 * |
| `qos_dynamic_adaptive` | +0.95 | +0.40 | +0.16 | +0.04 | +18.6 * |
| `qos_markov` | +0.50 | +0.00 | +0.12 | +0.19 | +18.6 * |
| `qos_markov_200ms` | +1.24 * | +0.30 | +0.38 | +0.45 | +18.6 * |
| `qos_markov_l95` | +0.46 | +0.20 | +0.43 | +0.34 | +18.5 * |
| `qos_auto_port` | +1.97 * | +0.53 | +1.03 | +0.38 | +18.5 * |

Goodput is also significantly higher than the static cap in the other three phases for every
controller (+6 to +26 MB/s); see `tables/timevarying_analysis.txt`.

### What to say

- Every limiter significantly beats no limit in all three attack phases, by 2–5 ms at P99.
- **No controller protects better than the static cap.** On victim latency the best a controller
  does is tie it.
- **Controllers are work-conserving.** In the calm phase the heavy tenant's traffic is harmless
  (victim P99 is the same in every arm). Every controller lets all 43.7 MB/s of it through. The static
  cap and `tc police` cut it to 25 MB/s for no benefit to anyone.
- **The cost is reaction time.** Under one-second bursts the controllers that poll every 200 ms
  (`qos_dynamic_fixed`, `qos_markov_200ms`, `qos_auto_port`) are 1.2–2.0 ms worse than the static
  cap, significantly.
- The two Markov controllers that poll every 100 ms (`qos_markov`, `qos_markov_l95`) stay within
  0.5 ms of the static cap in every attack phase, none significant, while keeping the efficiency
  gain.
- The predictor itself adds nothing measurable at equal polling: at 200 ms the Markov controller
  (+1.24 ms in bursts) and the plain reactive controller (+1.28 ms) are the same. The Markov arm's
  advantage comes with its 100 ms polling.
- Forgetting (`qos_markov_l95`) made no measurable difference from the plain Markov controller in
  these scenarios.
- Adaptive polling (`qos_dynamic_adaptive`) is +0.95 ms in bursts, between the two, and not
  significantly different from the static cap.
- `tc police` protects best in every attack phase.

Sources: `tables/timevarying_summary.csv`, `tables/timevarying_tests.csv`,
`tables/timevarying_analysis.txt`.

---

## 6. Automatic detection

### 6.1 Small testbed (workload 09): spoofing and identity

200 trials on the original 3-victim, 1-attacker testbed, 5 scenarios × 4 arms × 10 repetitions,
5 Nov. **This run predates the limiter rewrite. Its detection results are usable; its latency
numbers are not** (section 4.3 of `04_limitations_and_open_work.md`).

| Scenario | What it tests | `qos_auto_port` | `qos_auto_ip` |
|---|---|---|---|
| `single` | one flooder | precision 1.0, recall 1.0, 0.37 s | precision 1.0, recall 1.0, 0.44 s |
| `two_attackers` | a second flooder that a configured list would trust | 1.0, 1.0, 0.38 s | 1.0, 1.0, 0.46 s |
| `spoof` | `hping3 --rand-source` | 1.0, 1.0, 0.37 s | **precision 0: attacker not identified** |
| `heavy_victim` | the victim itself busy (500 requests/s) under attack | 1.0, 1.0, 0.38 s; victim not flagged | 1.0, 1.0, 0.38 s |
| `benign_heavy` | no attacker, one busy legitimate tenant | 0 false-positive ticks | 0 false-positive ticks |

Identity by source IP fails when the attacker randomises its source address, because the traffic
fragments across thousands of sources. Identity by ingress port holds. This is the reason
`qos_auto_port` is the automatic arm used everywhere else.

Source: `tables/w09_classification.csv`.

### 6.2 At 100 containers

| Run | Scenario | Precision | Recall | Falsely flagged tenants | Time to classify |
|---|---|---|---|---|---|
| Final benchmark | 1 flood | 1.0 | 1.0 | 0 | 0.69 s |
| | 5 floods | 1.0 | 1.0 | 0 | 0.49 s |
| | 5 small-packet floods | 1.0 | 1.0 | 0 | 0.29 s |
| | no attack | – | – | 0 | – |
| Density, every N from 1 to 100 | 1 flood | 1.0 | 1.0 | 0 | 0.49–0.69 s |
| | growing attack | 1.0 | 1.0 | 0 | 0.29–0.69 s |
| | no attack | – | – | 0 | – |

Detection was perfect at every density with one calibration, and classification takes two to four
200 ms ticks.

### 6.3 Where it fails: legitimate heavy senders

In those runs every legitimate tenant sends the same light load (20 requests/s). The industry
benchmarks load a few tenants heavily, and there the detector flags them. Counts are controller
ticks in which a legitimate tenant was flagged, out of ticks with traffic, from the 5-repetition run
of 13–14 Nov:

| Benchmark | Cell | Ticks with a legitimate tenant flagged |
|---|---|---|
| Istio profile (1000 requests/s) | all six cells | 0 of 9,652 |
| netperf TCP_RR, 8 flows, idle | | 740 of 746 |
| netperf TCP_CRR, 8 flows, idle | | 766 of 784 |
| netperf TCP_STREAM, 1 flow, idle | | 482 of 749 |
| netperf TCP_STREAM, 8 flows, idle | | 665 of 750 |
| memcached, idle | 10k–40k operations/s | 0 |
| memcached, idle | 60k, 80k, saturation | 752–760 of 770 |
| memcached, noisy | 40k and above | 751–764 of about 883 |

Effect: bulk TCP throughput fell to about a third of the plain bridge (3.0 against 9.1 Gbit/s with
one flow, 10.8 against 30.3 Gbit/s with eight). The request–response and memcached latencies were
not visibly hurt, because the cap was loose for that traffic.

**What to say:** the detector separates attackers from victims perfectly when victims are light. It
cannot tell a legitimate bulk sender from an attacker, because rate is its only signal. This is the
fairness-not-exemption property of max-min fair share, and it is the main limitation of the
automatic arm.

These counts come from controller logs, so they do not depend on the number of repetitions. The
10-repetition rerun will confirm them.

---

## 7. Overheads

**Figure:** `figures/fig08_density_overheads.png`.

### eBPF program run time per packet (ns, mean)

| Arm | No attack, N = 1 → 100 | Growing attack, N = 1 → 100 |
|---|---|---|
| `qos_open` | 96 → 121 | 72 → 78 |
| `qos_static` | 166 → 242 | 217 → 286 |
| `qos_dynamic_fixed` | 166 → 241 | 214 → 283 |
| `qos_auto_port` | 238 → 321 | 284 → 375 |
| `sidecarless` | 7,916 → 36,288 | about 100,000 at every N |

### Controller tick time (ms, 99th percentile) against a 200 ms period

| Controller | N = 1 | N = 50 | N = 100 |
|---|---|---|---|
| Reactive (`qos_dynamic_fixed`) | 0.13–0.20 | 0.31–0.32 | 0.43–0.44 |
| Automatic (`qos_auto_port`) | 0.29–0.39 | 0.95–1.07 | 1.55–1.98 |

In the final benchmark: reactive 0.41–0.46 ms, Markov 0.51–0.53 ms, automatic 1.46–1.75 ms.

### Other costs

| Cost | N = 1 | N = 50 | N = 100 |
|---|---|---|---|
| Attaching an arm (s) | 0.6 | 5.0 | 10.5 |
| Starting the containers (s) | 5.2 | 20.6 | 36.2 |
| Tenant CPU busy, no attack, every per-tenant arm | 0.004 | 0.26 | 0.54 |

### What to say

- A limiter adds roughly 70–210 ns per packet over the same data plane with no limit, and
  automatic accounting about 70–90 ns more.
- A controller tick uses under 1 % of its 200 ms period at 100 containers, and grows linearly with
  the number of tenants.
- Without attack, tenant CPU use is identical with and without a limiter.
- `sidecarless` spends about 100 µs per packet under attack, three orders of magnitude more, on its
  50 contended updates of one shared map key.

Source: `tables/densityB_summary.csv` (metrics `prog_ns_mean`, `ctrl_tick_ms_p99`, `attach_s`,
`cpu_busy_tenants`), `tables/densityB_setup.csv`.

---

## 8. Istio benchmark against an Envoy sidecar

480 trials: 8 arms × idle/noisy × 3 variants × 10 repetitions. fortio, HTTP/1.1, 1 KB payload,
1000 requests/s, one client → server pair on the 100-tenant testbed.

**Figures:** `figures/fig12_istio_p99.png` (without `sidecarless`, the one to use),
`figures/fig13_istio_p99_with_sidecarless.png`.

### 8.1 P99 (ms)

| Arm | Idle, 16 conn. | Idle, 16 conn. uniform | Idle, 4 conn. | Noisy, 16 conn. | Noisy, 16 conn. uniform | Noisy, 4 conn. |
|---|---|---|---|---|---|---|
| `bare` | 0.668 | 0.199 | 0.285 | 1.073 | 0.285 | 0.388 |
| `envoy_sidecar` | 3.839 | 0.979 | 1.528 | 6.319 | 1.590 | 2.343 |
| `envoy_sidecar_mtls` | 3.989 | 1.015 | 1.392 | 6.838 | 1.674 | 2.174 |
| `sidecarless` | 1.582 | 0.262 | 0.490 | 95.6 | 99.0 | 86.7 |
| `qos_open` | 0.687 | 0.199 | 0.288 | 1.163 | 0.294 | 0.399 |
| `qos_static` | 0.678 | 0.199 | 0.290 | 0.938 | 0.249 | 0.362 |
| `qos_markov` | 0.690 | 0.199 | 0.290 | 0.942 | 0.263 | 0.380 |
| `qos_auto_port` | 0.689 | 0.199 | 0.291 | 0.960 | 0.268 | 0.381 |

### 8.2 Median (P50, ms)

| Arm | Idle, 16 conn. | Idle, uniform | Idle, 4 conn. | Noisy, 16 conn. | Noisy, uniform | Noisy, 4 conn. |
|---|---|---|---|---|---|---|
| `bare` | 0.288 | 0.099 | 0.152 | 0.407 | 0.152 | 0.195 |
| `envoy_sidecar` | 2.791 | 0.564 | 1.239 | 4.469 | 0.832 | 1.583 |
| `envoy_sidecar_mtls` | 2.950 | 0.582 | 1.077 | 4.722 | 0.872 | 1.578 |
| `qos_open` | 0.294 | 0.114 | 0.153 | 0.455 | 0.154 | 0.221 |
| `qos_static` | 0.296 | 0.122 | 0.153 | 0.370 | 0.150 | 0.180 |

### 8.3 Tests on P99 (Hodges–Lehmann shift, ms)

Family A, each arm against `bare` (42 tests, threshold 0.0012):

| Arm | Idle, 16 | Idle, uniform | Idle, 4 | Noisy, 16 | Noisy, uniform | Noisy, 4 |
|---|---|---|---|---|---|---|
| `envoy_sidecar` | +3.17 * | +0.78 * | +1.24 * | +5.27 * | +1.31 * | +1.95 * |
| `envoy_sidecar_mtls` | +3.32 * | +0.82 * | +1.11 * | +5.76 * | +1.39 * | +1.78 * |
| `sidecarless` | +0.91 * | +0.06 * | +0.20 * | +94.6 * | +98.7 * | +86.3 * |
| `qos_open` | +0.017 | +0.000 | +0.002 | +0.089 | +0.010 * | +0.011 * |
| `qos_static` | +0.009 | +0.000 | +0.004 | −0.119 | −0.036 * | −0.027 |
| `qos_markov` | +0.015 | +0.000 | +0.005 | −0.131 | −0.021 * | −0.009 |
| `qos_auto_port` | +0.023 | +0.000 | +0.005 | −0.108 | −0.016 * | −0.008 |

Family B, each of our arms against `envoy_sidecar` (30 tests, threshold 0.0017):

| Arm | Idle, 16 | Idle, uniform | Idle, 4 | Noisy, 16 | Noisy, uniform | Noisy, 4 |
|---|---|---|---|---|---|---|
| `qos_open` | −3.15 * | −0.78 * | −1.24 * | −5.17 * | −1.30 * | −1.94 * |
| `qos_static` | −3.16 * | −0.78 * | −1.24 * | −5.34 * | −1.34 * | −1.97 * |
| `qos_markov` | −3.16 * | −0.78 * | −1.24 * | −5.38 * | −1.33 * | −1.97 * |
| `qos_auto_port` | −3.15 * | −0.78 * | −1.24 * | −5.36 * | −1.32 * | −1.98 * |
| `sidecarless` | −2.26 * | −0.72 * | −1.04 * | +89.2 * | +97.5 * | +84.5 * |

### 8.4 What to say

- **Overhead when idle.** Our eBPF arms add 0.00–0.02 ms to P99 over a plain bridge, not
  significant. Two Envoy sidecars add 3.2 ms on Istio's 16-connection profile, 1.2 ms with
  4 connections and 0.8 ms when requests are spread evenly, all significant.
- **Against Envoy.** Every QoS arm is significantly lower than `envoy_sidecar` in all six cells:
  0.8–3.2 ms idle, 1.3–5.4 ms under floods.
- **The Envoy baseline is credible.** Its overhead on the 16-connection profile (2.5 ms at the
  median, 3.2 ms at P99) is in line with Istio's published sidecar figures (2.65 ms at P90, 2.91 ms
  at P99). Most of it is queueing: fortio fires all 16 connections at the same instant into two
  worker threads.
- **mTLS** adds about 0.15 ms at P99 on the 16-connection profile. It is not consistently higher
  than plain Envoy in the 4-connection cells, and the two were not tested against each other.
- **Envoy gives no protection from the flood.** Its P99 rises from 3.84 to 6.32 ms under the noisy
  condition, a larger absolute rise than `bare` (0.67 to 1.07 ms).
- **Protection is small in this benchmark.** The floods raise `bare` P99 by only 0.4 ms. The limiters
  end 0.11–0.13 ms below `bare` on the 16-connection profile, which is not significant; only the
  uniform cell is significant (0.02–0.04 ms). Only one client–server pair carries load here, so
  there is far less contention than in sections 2–5. The protection claim rests on those sections.
- **A small cost under flood.** `qos_open` is 0.01 ms above `bare` in two noisy cells,
  significantly: the eBPF program runs on every flood packet.
- **`sidecarless` collapses under floods:** 87–99 ms at P99, completing about 350, 345 and 95 of the
  1000 requests/s in the three cells.

### 8.5 Limits of the comparison

- Everything ran on one machine, so the comparison with Envoy is like for like. Vendor-published
  numbers come from other hardware, Kubernetes and several nodes; they are context, not a comparison.
- Envoy parses HTTP, can route and retry, and in the mTLS arm encrypts. The eBPF path does none of
  that. The comparison is of the cost of the request path, not of equal features.
- There is no iptables redirection in the testbed's Envoy path, which real Istio adds. Real sidecar
  overhead would be somewhat higher.

Sources: `tables/istio_summary.csv`, `tables/istio_overhead.csv`, `tables/istio_tests.csv`,
`tables/istio_analysis.txt`.

---

## 9. netperf and memcached: preliminary, 5 repetitions

> **Do not cite these as tested results.** With 5 repetitions the smallest possible p-value is about
> 0.008, above the corrected thresholds (0.0008–0.0014), so none of the 144 tests could be
> significant. A 10-repetition rerun started on 14 Nov and will replace this section. The numbers
> below are descriptive and are given so the direction of the result is known.

### netperf (idle, medians)

| Test | `bare` | `qos_open` | `qos_static` | `qos_auto_port` | `envoy_sidecar` | `sidecarless` |
|---|---|---|---|---|---|---|
| TCP_RR, 1 flow, P99 (µs) | 29 | 29 | 29 | 29 | 149 | 47 |
| TCP_RR, 1 flow, transactions/s | 37,620 | 40,446 | 40,035 | 40,865 | 7,594 | 23,437 |
| TCP_CRR, 1 flow, P99 (µs) | 118 | 128 | 128 | 129 | 575 | 297 |
| TCP_CRR, 1 flow, transactions/s | 11,169 | 10,476 | 10,430 | 10,147 | 2,151 | 5,277 |
| TCP_STREAM, 8 flows (Mbit/s) | 30,285 | 29,236 | 30,215 | 10,759 | 14,766 | 7,403 |

### memcached

| Metric | `bare` | `qos_open` | `qos_static` | `qos_auto_port` |
|---|---|---|---|---|
| P99 at 10k operations/s, idle (ms) | 0.159 | 0.183 | 0.183 | 0.175 |
| P99 at 20k, idle | 0.263 | 0.295 | 0.279 | 0.263 |
| P99 at 40k, idle | 0.407 | 0.423 | 0.479 | 0.447 |
| P99 at 60k, idle | 0.791 | 1.007 | 1.055 | 1.263 |
| Peak throughput, idle (operations/s) | 91,376 | 86,648 | 86,190 | 84,990 |
| P99 at 40k, noisy (ms) | 0.855 | 1.359 | 0.751 | 0.831 |
| Peak throughput, noisy (operations/s) | 66,652 | 59,677 | 71,303 | 69,134 |

### Direction of the result

- Request–response latency through the eBPF path is the same as the plain bridge; connection setup
  costs about 10 µs more. Envoy is about 5 times slower on both.
- Bulk throughput with eight flows is unchanged by `qos_open` and `qos_static`, and halved by Envoy.
  Single-flow stream results vary too much between repetitions to read.
- `qos_auto_port` cuts bulk throughput to a third because it flags the sender (section 6.3).
- Near memcached's saturation point the eBPF arms cost 0.2–0.5 ms at P99 and 5–7 % of peak
  throughput.
- Under floods, the limiters recover peak memcached throughput to above the plain bridge's level.

Data: `test_bed_qos/workloads/11_benchmarks/results/netperf_B_N5_20261113_183423/` and
`memcached_B_N5_20261113_183423/`.
