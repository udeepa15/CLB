# Environment Setup — How things are configured and why

This document explains how the experiment environment is prepared, what each setup step does, and why it's necessary.

## Purpose
The test harness runs multiple containerized tenants and a noisy workload on a single host to evaluate isolation controls (tc, eBPF, adaptive). The environment setup ensures reproducible network, CPU, and eBPF capabilities on the host so experiments run reliably.

## Quick commands
- Prepare system and packages:

```bash
cd ebpf-noisy-neighbor
sudo ./environments/base-setup.sh
```

- Build rootfs bundles for containers:

```bash
sudo ./containers/setup-rootfs.sh
```

- Run the full matrix of experiments:

```bash
sudo ./scripts/run-all-experiments.sh ./config.yaml
```

- Stop running containers and detach eBPF:

```bash
sudo ./scripts/stop-containers.sh
sudo ./ebpf/tc/attach.sh detach
```

## What `environments/base-setup.sh` does (why + how)
- Installs packages required by the test harness: `runc`, `iproute2` (for `ip`, `tc`), `clang/llvm` (to build eBPF programs), `python3`, `curl`, `jq`, and any other tooling.
  - Why: tests launch runC containers, configure network interfaces and qdiscs, compile/attach eBPF programs, and run Python analysis.
- Applies kernel and network tuning via `sysctl` (example: enabling IP forwarding, increasing socket buffers, adjusting CFS/cgroup settings as needed).
  - Why: ensures predictable networking behavior and sufficient resources for high request rates.
- Ensures required kernel features for eBPF and traffic control are available (bpfilter/bpf, cls_act / cls_u32, act_* helpers). If missing, the script warns or exits.

Run the script as root (it modifies system state). Inspect the script if you need to adapt settings for your environment.

## What `containers/setup-rootfs.sh` does (why + how)
- Prepares minimal root filesystem templates (the repository includes an `alpine-rootfs` template).
- Copies the template into `containers/runtime/<name>` bundles and injects container-specific configuration files (`config.json`, startup scripts, workload entrypoints).
- Why: runC expects a bundle directory with a `rootfs/` and `config.json` — this step automates producing reproducible containers for `tenant1`, `tenant2`, and `noisy`.

Output to inspect:
- `containers/runtime/inventory.csv` — lists container names, roles, and IPs used by orchestrator scripts.
- `containers/runtime/cgroup_ids.csv` — (written at runtime) maps container names to cgroup IDs (used when `identity_mode=cgroup`).

## Networking setup (`networking/setup-network.sh`, `teardown-network.sh`)
- Creates a Linux bridge (default: `clb-br0`) and one veth pair per container; assigns the container-side veth to the container namespace and host-side to the bridge.
- Configures IP addresses consistent with the inventory (e.g., `10.0.0.*`) and routing rules.
- Why: provides an isolated L2/L3 test network where latency, shaping, and eBPF policies can be applied deterministically.

Verify with:
```bash
ip link show clb-br0
ip addr show dev veth-tenant1  # or your veth names
bridge link
```

## eBPF/tc (`ebpf/tc/build.sh`, `ebpf/tc/attach.sh`)
- `build.sh` compiles C programs under `ebpf/tc/` into object files usable by `tc` (via `tc clsact` or `tc netem` hooks) or `bpftool`.
- `attach.sh` attaches or detaches the selected eBPF strategy to the host interface (usually the host-side veth or bridge interface). Strategies include `dropper`, `rate_limit`, `priority`, and an `adaptive` mode.
- Why: eBPF implementations can perform packet-level decisions with lower overhead and finer granularity than classic `tc` alone.

Adaptive mode notes:
- Adaptive mode runs the `core/adaptive_controller.py` process which monitors latency and adjusts BPF maps (via `core/bpf_map_ctl.py`) to apply per-identity controls.
- Identity can be `ip` (source IP) or `cgroup` (container cgroup id) depending on `config.yaml`.

## Orchestration: `scripts/run-single-experiment.sh` and `run-all-experiments.sh`
- `run-single-experiment.sh` performs the full lifecycle for one experiment:
  1. Cleanup any previous adaptive controller runs and stop containers.
  2. Start containers (`runc run -d`) from `containers/runtime/*` bundles.
  3. Configure networking (bridge + veths) and write `containers/runtime/inventory.csv`.
  4. Optionally apply isolation: `none`, `tc` (static shaping on the noisy iface), `ebpf` (attach chosen eBPF program), or `adaptive` (run controller + set BPF maps).
  5. Run the noisy workload inside the `noisy` container and HTTP servers in tenant containers.
  6. Run the client workload (`workloads/client.sh`) to send requests and collect latencies.
  7. Collect metrics and append to `results/processed/` and `results/raw/`.
  8. Stop containers and teardown network.
- `run-all-experiments.sh` uses `scripts/run_experiment_matrix.py` to enumerate the experiment matrix (based on `config.yaml`) and runs each `run-single-experiment.sh` invocation sequentially. A progress indicator is printed to the terminal.

Why this approach:
- Sequential orchestration avoids noisy parallel resource contention on a single host and ensures stable per-experiment baselines.
- Using `runc` bundles gives you container isolation without a heavy container runtime (lighter and more reproducible for experiments).

## Workloads
- `workloads/client.sh` is the client driver: sends a configured number of HTTP requests to tenant servers and writes latency timeseries and summary output.
- `workloads/noisy.sh` (or the noisy container entrypoint) generates CPU and/or network pressure to simulate a noisy neighbor.
- `workloads/victim.sh` runs a lightweight HTTP server inside tenant containers (the scripts use a Python HTTP server by default).

## Data flow and artifacts
- Raw artifacts: `results/raw/*.latency`, `results/raw/*.timeseries.csv`, `results/raw/*.log`, `results/raw/*.txt` (per-experiment files)
- Processed: `results/processed/summary.csv` and `results/processed/results.csv` (updated per experiment)
- Final outputs (after `core/finalize_results.py`): `results/final/*.csv`, `results/final/graphs/*.png`, `results/final/logs/*.log`

## Verification & troubleshooting (quick checks)
- Are containers running?
```bash
runc --root /run/runc-ebpf-noisy-neighbor list
```
- Are the network interfaces present?
```bash
ip link show type veth
bridge link
```
- Is tc or eBPF attached?
```bash
tc qdisc show dev veth-noisy
tc filter show dev clb-br0 ingress
sudo bpftool prog show  # lists loaded eBPF programs
```
- Confirm tenant HTTP endpoints respond:
```bash
curl -sS http://10.0.0.2:8080/ -o /dev/null && echo OK
```
- Inspect recent experiment logs:
```bash
tail -n 200 results/raw/exp_XXXXX*.txt
```
- Suppress non-fatal `jq` warnings: some stop/cleanup commands attempt to iterate an empty list; these warnings are harmless but can be silenced by checking for empty output before piping into `jq`.

## Cleanup and safety notes
- Always run orchestration scripts as root (`sudo`) because they create network devices and manage cgroups.
- To abort a running experiment gracefully: hit Ctrl-C; the scripts trap EXIT and attempt cleanup (stop containers, detach eBPF, teardown network). If things remain, run:

```bash
sudo ./scripts/stop-containers.sh
sudo ./ebpf/tc/attach.sh detach
sudo ./networking/teardown-network.sh
```

## Tuning and performance tips
- `config.yaml` sets `requests_per_client` and `iterations` — reduce these for quicker local testing.
- Increase `sample_interval_s` and `p99_threshold_ms` only if you intend to relax controller sensitivity.
- Run a small subset via `configs/research_matrix.yaml` or explicitly defined `scenarios:` to speed up development.

---

If you want, I can:
- Add this as a linked section in the `GETTING_STARTED.md` (quick edit),
- Commit and push the new doc for you, or
- Expand any section with command-by-command debugging examples tuned to your host.

Which would you like next?
