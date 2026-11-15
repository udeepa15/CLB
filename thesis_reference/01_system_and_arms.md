# 1. The system and the arms

## 1.1 Research question

In a sidecarless service mesh, many tenants share one kernel packet path on a node. A tenant that
floods the node slows everyone else's requests. The research asks whether rate limiting done in eBPF
at each tenant's port can keep the other tenants' tail latency low, what it costs, and whether a
controller that sets the limit automatically does better than a fixed limit.

## 1.2 Testbed

- One Linux host with 8 CPUs. All tenants hang off one Linux bridge, `br-mesh`, each through its own
  veth pair. Each tenant lives in its own network namespace.
- A *victim* tenant is a runc container that runs an HTTP server on port 8080. All victim containers
  share one read-only root filesystem.
- An *attacker* tenant sends a UDP flood with hping3 at one victim server.
- The topology comes from one tenants file. `tenants/scale100.json` has 95 victims (10.0.1.x) and
  5 attackers (10.0.128.x) on 10.0.0.0/16. `tenants/default.json` is the original small testbed:
  3 victims and 1 attacker.
- The roles in the tenants file (who is a victim, who is an attacker) are oracle knowledge. Only the
  static, reactive and Markov arms use them. The automatic arms are given no roles; the roles are
  then used only to score their decisions.

Code: `test_bed_qos/tenants.py`, `topology.py`, `containers.py`.

## 1.3 Data plane

Each tenant's bridge-side veth gets eBPF programs on the tc `clsact` hook in direct-action mode. For
100 tenants that is about 200 hooks.

- `classifier_ingress` runs on packets entering the bridge from a tenant. This is the only place a
  limit is enforced, so each packet is charged once.
- `classifier_telemetry` runs on the other direction and only counts.

Programs: `ebpf_qos_dynamic.c` (limit by source IP), `ebpf_qos_auto.c` (accounting per IP and per
port, limit by port then by IP), `ebpf_qos_tiered.c` (interim design), `ebpf_mesh_router.c`
(`sidecarless` baseline). Shared code is in `qos_limiter.h`.

## 1.4 The limiter (`qos_limiter.h`)

The limiter was redesigned during this work because the earlier one did not enforce its cap (see
1.9). The current design:

- **Configuration and state are separate maps.** The controller writes only `rate_cfg`
  (`rate_bytes_per_sec`, `max_burst_bytes`). The kernel alone writes `rate_state`. A controller
  update can therefore never refill a bucket.
- **One bucket per capped tenant, shared by all CPUs,** guarded by a `bpf_spin_lock`. The cap is
  exact however many CPUs carry the tenant's packets.
- **Uncapped tenants pay almost nothing.** A tenant with rate 0 (every victim) costs one read-only
  hash lookup and one per-CPU counter increment. It never takes a lock. Only a capped tenant's own
  packets contend for its lock.
- **The bucket is GCRA** (generic cell rate algorithm, virtual scheduling). It keeps one timestamp,
  the theoretical arrival time `tat_ns`. For a packet of `len` bytes at time `now`:
  `cost = len × 10⁹ / rate`, `tau = burst × 10⁹ / rate`; the packet is dropped if
  `tat + cost > now + tau`, otherwise `tat` advances by `cost`. This is equivalent to a token bucket
  of depth `burst` refilled at `rate`, without the refill multiplication that overflowed and the
  per-packet rounding loss of the old code.
- **Counters are per-CPU and cumulative** (`packets_passed`, `packets_dropped`, `bytes_passed`,
  `bytes_dropped`); user space never resets them.
- **It fails open:** if the state map is full, the packet passes.
- **Burst rule:** every capped arm uses `burst = max(rate / 10, 131072)` bytes.

Controllers read and write maps through the `bpf(2)` system call from Python (`bpfmap.py`), not by
forking `bpftool`.

## 1.5 Controllers

All controllers read a packet counter from the kernel and compute *hits per second*, H. H counts
eBPF program runs; it is a packet-rate signal, **not** a measure of lock contention.

**Cap function** (used by the reactive, Markov and automatic controllers), a Hill function:

```
r(H) = r_max / (1 + (H / H0)^gamma)      clamped to [r_min, r_max]
r_max = 50 MB/s,  r_min = 500 KB/s,  gamma = 2,  H0 = calibrated knee
```

The code calls this a Stackelberg leader–follower equilibrium; in practice it is this closed-form
curve, and the thesis should describe it as such.

| Controller | File | Behaviour |
|---|---|---|
| Reactive, fixed polling | `qos_controller.py --no-adaptive --interval 0.2` | Every 200 ms, sets every attacker's cap to r(H) |
| Reactive, adaptive polling | `qos_controller.py --interval 0.2` | Same rule; the polling interval moves between 20, 200 and 1000 ms according to how fast H is changing (spike threshold 5000 hits/s², flat threshold 500 hits/s², 5 calm cycles before relaxing) |
| Markov (DTMC) | `markov_experiments/qos_controller_markov.py` | Discretises H into four states: LOW (< H0/3), MODERATE (< H0), HIGH (< 2·H0), SEVERE. Learns a transition matrix online, starting empty each trial. If the predicted probability of moving to HIGH or SEVERE is at least 0.60, it caps pre-emptively as if H were at least 7/3·H0; otherwise it uses r(H). Polls every 100 ms |
| Markov with forgetting | same, `--forget-lambda` or `--window` | Old transitions decay (λ = 0.99, 0.95, 0.90) or fall out of a sliding window (50), so the model can follow a change of regime |
| Automatic | `qos_controller_auto.py` | See 1.6 |

## 1.6 Automatic attacker detection

The automatic arms are given no victim or attacker list. Each 200 ms tick the controller:

1. reads per-tenant packet counters from the kernel;
2. computes the max-min fair share of the node's capacity: the water level `s` such that
   `sum_i min(rate_i, s) = capacity`. If total demand fits within capacity, nobody is flagged;
3. marks a tenant *over* when `rate_i > s × (1 + margin)`, with margin 0.25;
4. flags a tenant as heavy after 2 consecutive over ticks and releases it after 5 consecutive ticks
   that are not over;
5. caps only the heavy tenants, using the same Hill function as the reactive controller.

So the automatic arm differs from the reactive arm only in *who* gets capped.

Identity can be the source IP (`qos_auto_ip`), which an attacker can spoof, or the tenant's ingress
bridge port (`qos_auto_port`), which cannot be spoofed from inside the tenant.

The design guarantee is fairness, not exemption: a legitimate tenant that sends more than its fair
share while the node is overloaded *is* flagged. This is visible in the industry benchmarks
(`03_results.md` section 8.4).

Code: `fairshare.py`, `qos_controller_auto.py`, `ebpf_qos_auto.c`.

## 1.7 Calibration

Two parameters depend on the machine and on the number of tenants: the fair-share `capacity_pps` of
the automatic arm, and the knee `H0` of the Hill function (which also sets the Markov state
boundaries). They are measured, not hand-picked.

Procedure (`calibrate.py`): run the attack at increasing rates with no limiter (`qos_open`), and find
by interpolation the rate at which pooled victim P99 leaves a 25 % band above its no-attack value.

| Configuration | No-attack P99 | P99 limit (×1.25) | `capacity_pps` | `H0` (hits/s) | File |
|---|---|---|---|---|---|
| B (used for all main results) | 2.81 ms | 3.51 ms | 75,797 | 151,591 | `tables/calibration_configB.json` |
| A | 1.46 ms | 1.82 ms | 37,296 | 74,591 | `tables/calibration_configA.json` |

The calibration was made once at 100 containers and reused at every density from 1 to 100.

## 1.8 The arms

An *arm* is one configuration of data plane plus controller. All arms run in the same harness
(`arms.py`), so they differ only in the factors below.

| Arm | Data plane | Limit | Controller | Polling |
|---|---|---|---|---|
| `bare` | plain Linux bridge, nothing attached | none | none | – |
| `qos_open` | `ebpf_qos_dynamic.c` with an empty configuration | none | none | – |
| `qos_static` | `ebpf_qos_dynamic.c` | fixed 5 MB/s per attacker | none | – |
| `qos_tc_police` | kernel `tc police`, no eBPF | fixed 5 MB/s per attacker | none | – |
| `qos_dynamic_fixed` | `ebpf_qos_dynamic.c` | r(H) | reactive | 200 ms |
| `qos_dynamic_adaptive` | `ebpf_qos_dynamic.c` | r(H) | reactive | 20/200/1000 ms |
| `qos_markov` | `ebpf_qos_dynamic.c` | r(H), pre-emptive | DTMC | 100 ms |
| `qos_markov_200ms` | same | same | DTMC | 200 ms |
| `qos_markov_l95` | same | same | DTMC, λ = 0.95 | 100 ms |
| `qos_auto_port` | `ebpf_qos_auto.c` | r(H) on flagged ports | automatic | 200 ms |
| `qos_auto_ip` | `ebpf_qos_auto.c` | r(H) on flagged source IPs | automatic | 200 ms |
| `qos_tiered` | `ebpf_qos_tiered.c` (interim design) | fixed 5 MB/s, shared entry with no lock | none | – |
| `sidecarless` | `ebpf_mesh_router.c` | none; one shared map key updated 50 times per packet | none | – |
| `envoy_sidecar` | plain bridge plus two Envoy proxies per request | none | none | – |
| `envoy_sidecar_mtls` | same, with mutual TLS between the proxies | none | none | – |

What each comparison isolates:

- `bare` vs `qos_open`: the cost of the eBPF data plane with no limiting.
- `qos_open` vs any limiter: the effect of limiting. This is the main comparison.
- `qos_static` vs `qos_dynamic_fixed`: the effect of feedback control.
- `qos_dynamic_fixed` vs `qos_dynamic_adaptive`: adaptive polling.
- `qos_dynamic_fixed` vs `qos_markov_200ms`: the DTMC predictor at an equal polling rate.
- `qos_dynamic_fixed` vs `qos_auto_port`: who gets capped (configured list vs automatic detection).
- `qos_tiered` vs `qos_static`: the limiter design (unlocked shared entry vs exact bucket).
- `qos_open` vs `sidecarless`: the map design, with no limiting in either.
- `qos_static` vs `qos_tc_police`: an eBPF limiter vs the kernel's own policer.
- our arms vs `envoy_sidecar`: an existing sidecar solution on the same machine.

### The Envoy sidecar baseline (`envoy_sidecar.py`)

Istio's sidecar mode puts two proxies on every request. The testbed reproduces that path without
Kubernetes:

```
client app -> 127.0.0.1:15001 (client's Envoy) -> server_ip:15006 (server's Envoy) -> 127.0.0.1:8080 (server app)
```

- Envoy 1.31.2, two worker threads per proxy (`--concurrency 2`), as in Istio's published benchmark
  profile.
- HTTP ports use Envoy's HTTP connection manager (layer 7); raw TCP ports use `tcp_proxy` (layer 4).
- The mTLS variant adds mutual TLS between the two sidecars with a local throwaway CA.
- There is no iptables interception: the client addresses its local sidecar directly. Attack floods
  still go straight to the server's IP.

## 1.9 Defects found in the earlier design

These explain why the interim-report numbers are invalid.

| Defect | Effect |
|---|---|
| The controller rewrote the whole bucket on every tick, refilling it to a 10 MB burst | Dynamic arms let through about 5×, 10× and 22× the cap at 200, 100 and 20 ms polling |
| Every hook a packet crossed charged it | Static arms delivered close to nothing instead of the cap |
| One bucket per CPU | The effective cap was the rate times the number of CPUs carrying the traffic |
| Refill arithmetic | Could overflow after a long idle period, and lost a fraction of a token per packet |
| fortio's default 1 ms histogram buckets | Sub-millisecond P99 differences could not be measured |
| Automatic capacity hand-set to 10,000 packets/s | Flagged all 95 victims at 100 tenants |
| `tc` baselines left their qdiscs behind | Later trials broke or were silently throttled |
| Significance corrected over about 1,000 tests | Nothing could ever be significant at 10 repetitions |

All eight are fixed in the code used for the results in this folder.
