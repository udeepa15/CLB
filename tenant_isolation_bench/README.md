# Tenant Isolation Bench

This folder contains a standalone Linux namespace + veth benchmark stack for comparing baseline, isolated eBPF, and shared-map eBPF contention models.

## Layout

- `bpf/counter_tc.c` - tc/clsact BPF program with `baseline`, `isolated`, and `shared` builds
- `scripts/deploy_workloads.sh` - creates 6 victim namespaces and 1 adversary namespace, launches lightweight HTTP servers, and tears everything down cleanly
- `scripts/run_experiments.sh` - runs the full benchmark matrix
- `scripts/microsecond_reporter.lua` - wrk2 output formatter with microsecond percentiles
- `scripts/trace_map_locks.bt` - bpftrace telemetry for map lookup contention
- `scripts/clean_and_summarize.py` - aggregates raw wrk2 logs into a summary CSV

## Namespace Topology

- Victims: `v_netns_1` through `v_netns_6`
- Adversary: `v_netns_adv`
- Host interfaces: `veth_v1` through `veth_v6`, plus `veth_adv`
- Subnets: `10.200.1.0/24` through `10.200.6.0/24` for victims, and `10.200.7.0/24` for the adversary to keep every tenant on a unique subnet

## Prerequisites

Install the usual tooling first:

```bash
sudo apt install -y clang llvm make bpftool iproute2 python3 wrk2 perf bpftrace libbpf-dev libelf-dev jq
```

## Build

```bash
cd tenant_isolation_bench
make
```

## Deploy Workloads

Start the namespace and HTTP server set:

```bash
sudo ./scripts/deploy_workloads.sh start
```

Stop everything:

```bash
sudo ./scripts/deploy_workloads.sh stop
```

Restart from a clean slate:

```bash
sudo ./scripts/deploy_workloads.sh restart
```

## Run The Matrix

```bash
sudo ./scripts/run_experiments.sh
```

This sweeps:

- `baseline`, `isolated`, and `shared`
- active victim counts `1`, `3`, and `5`
- attacker rates from `0` to `45000` RPS

## Outputs

- Raw wrk2 logs: `results/raw/`
- bpftrace captures: `results/raw/bpftrace_*.txt`
- perf captures: `results/raw/perf_*.data`
- Summary CSV: `results/cleaned_matrix_metrics.csv`

## Notes

- The isolated mode attaches a separate tc program instance per interface.
- The shared mode loads a single pinned tc program instance and reuses it across all selected interfaces, while the BPF map is pinned under `/sys/fs/bpf/shared_counter_map`.
- The scripts are designed to be idempotent and to clean up namespaces, veth pairs, and tracer processes on failure.
