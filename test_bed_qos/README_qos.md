# Testbed QoS Extension (`test_bed_qos`) Architecture & Design Reference

> **Directory Goal**: This directory (`~/CLB/test_bed_qos/`) extends the core testbed with two new Quality of Service (QoS) architectural arms for Direction 1 research:
> 1. **`qos_tiered`**: Static Priority-Tier QoS Scheme (`ebpf_qos_tiered.c`)
> 2. **`qos_dynamic`**: Dynamic Stackelberg Game-Theoretic Rate Limiter (`ebpf_qos_dynamic.c` + `qos_controller.py`)
>
> **Isolation Guarantee**: The original dataset and scripts in `~/CLB/test_bed/` remain completely untouched and independently reproducible.

---

## 1. Architectural Arms Under Comparison

1. **`sidecarless` (Existing Baseline)**:
   - Unmodified eBPF classifier ([ebpf_mesh_router.c](file:///home/udeepa/CLB/test_bed_qos/ebpf_mesh_router.c)) with static global key (`shared_global_key = 0`) forcing kernel `htab_lock_bucket` spinlock contention.
2. **`sidecar` (Existing Reference)**:
   - Unmodified user-space `socat` reverse proxy baseline with `iptables` NAT redirection.
3. **`qos_tiered` (New Static Arm)**:
   - Three-tier static priority QoS classifier ([ebpf_qos_tiered.c](file:///home/udeepa/CLB/test_bed_qos/ebpf_qos_tiered.c)).
   - Prioritizes L0 legitimate victim traffic while enforcing rate caps on lower tiers (L1/L2).
4. **`qos_dynamic` (New Dynamic Arm)**:
   - Userspace Stackelberg game-theoretic controller ([qos_controller.py](file:///home/udeepa/CLB/test_bed_qos/qos_controller.py)) reading real-time eBPF contention signals (`lock_latency_hist`, `update_counter_map`) and dynamically updating per-tenant rate limits in a sharded kernel eBPF token-bucket map ([ebpf_qos_dynamic.c](file:///home/udeepa/CLB/test_bed_qos/ebpf_qos_dynamic.c)).

---

## 2. Design Rationale & Literature Citations

Every major design decision in `test_bed_qos` is directly grounded in published research and our empirical root-cause findings:

### A. Alibaba Terway-QoS — Priority-Tier Design
- **Citation**: Alibaba Cloud Terway-QoS Container Network Design.
- **Rationale**: Implements three priority levels: **L0** (High/Victim), **L1** (Medium), **L2** (Low/Attacker-adjacent). L0 is guaranteed immediate transmission and allowed to reclaim up to full link bandwidth whenever lower tiers (L1/L2) are operating below their allocated share.

### B. Netflix — Hash Map Selection (`BPF_MAP_TYPE_HASH`)
- **Citation**: Netflix Engineering, *"Noisy Neighbor Detection with eBPF"*.
- **Rationale**: Demonstrates that standard `BPF_MAP_TYPE_HASH` significantly outperforms `BPF_MAP_TYPE_LRU_HASH` for high-throughput per-tenant counter and timestamp storage because `LRU_HASH` incurs internal global LRU list synchronization overhead under heavy packet rates.

### C. Beeswax (SIGCOMM '26) — Fast-Path Kernel Execution & Per-CPU Sharding
- **Citation**: *"Don't Stall Me Now: Hiding Memory Latency in eBPF"* (Beeswax, SIGCOMM '26).
- **Rationale**: Kernel-side eBPF programs must minimize inline computation and avoid serial memory lookup stalls. Per-tenant rate limits use `BPF_MAP_TYPE_PERCPU_HASH` / `PERCPU_ARRAY` sharded per CPU, allowing zero-contention fast-path token checks without blocking on cross-CPU locks.

### D. Our Empirical Root-Cause Finding — Per-Tenant Keying
- **Citation**: Experimental Root-Cause Analysis (`CLB/test_bed`).
- **Rationale**: Serializing packet lookups on a single global key (`shared_global_key = 0`) causes severe CPU core spinlock lockup (`htab_lock_bucket`). All new eBPF programs in `test_bed_qos` strictly use **per-tenant IP/ID keys**, eliminating single-hot-key spinlock bottlenecks.

---

## 3. Directory Map

```
~/CLB/test_bed_qos/
├── config.sh                   # Shared load configuration (50 QPS, 2 conns, 10s duration)
├── setup_topology.sh           # Data plane network setup (br-mesh, netns)
├── ebpf_mesh_router.c          # Sidecarless baseline eBPF classifier (untouched reference)
├── ebpf_qos_tiered.c           # New: Static priority-tier eBPF QoS program
├── ebpf_qos_dynamic.c          # New: Dynamic Stackelberg eBPF fast-path token-bucket program
├── qos_controller.py           # New: Userspace Stackelberg control loop process
├── collect_ebpf_stats.py       # Extended eBPF metrics collector
├── collect_cgroup_stats.py     # Container cgroup CPU throttling collector
├── collect_network_stats.sh    # Interface drop counter logger
├── collect_bpftrace_lock.bt    # Kernel htab_lock_bucket spinlock kprobe tracing script
├── run_multi_run_matrix.py     # 4-architecture matrix benchmark runner
├── plot_multi_run_matrix.py    # 4-architecture P99 plotter + controller timeline overlay
└── README_qos.md               # This architectural reference file
```
