# Workload 11 – industry benchmarks

The benchmarks that existing service meshes and eBPF datapaths publish, run on the 100-tenant testbed
(config B), with a real existing solution (Envoy sidecars) as an extra arm on the same machine.

| App | Replicates | Load | Primary metric |
|---|---|---|---|
| `istio` | Istio data-plane benchmark | fortio, HTTP/1.1, 1 KB payload, 1000 RPS, 16 (classic) or 4 (newer) connections, one client → server pair | p99 latency; latency added over `bare` |
| `netperf` | Cilium CNI benchmark | netperf TCP_RR (1 byte), TCP_CRR, TCP_STREAM; 1 and 8 pairs | p99 transaction latency; throughput |
| `memcached` | isolation papers (Heracles, Caladan) | memcached + memtier_benchmark, fixed QPS 10k–80k plus saturation | p99 vs offered load |

Arms:

- `bare`: plain Linux bridge, the no-mesh baseline.
- `envoy_sidecar` / `envoy_sidecar_mtls`: two Envoy proxies on every request, with 2 workers each, as in
  Istio's profile; L7 for HTTP, L4 for TCP.
- `sidecarless`, `qos_open`, `qos_static`, `qos_markov`, `qos_auto_port`: as in workload 10.

Conditions: `idle` (only the measured app), and `noisy` (5 attackers flood servers 1–5, which include the
measured servers).

Absolute numbers are not comparable with vendor publications, which use different hardware, Kubernetes and
multiple nodes. The comparison that counts is against `bare` and `envoy_sidecar` on this machine.

```
sudo setsid nohup bash workloads/run_workload_11.sh > w11.log 2>&1 < /dev/null &
python3 analyze_bench.py results/<run>          # as the normal user (root's python has no numpy)
```
