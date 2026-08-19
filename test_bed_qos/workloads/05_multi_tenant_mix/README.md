# Workload 05: Heterogeneous Multi-Tenant Co-location Mix

## 1. Literature Citation & Methodological Rationale
- **Citations**:
  - Alibaba Cluster Trace dataset (`github.com/alibaba/clusterdata`).
  - Lu et al., *"Imbalance in the cloud: An analysis on Alibaba cluster trace"* (2017).
- **Rationale**: Lu et al. document that real-world cloud clusters exhibit severe workload heterogeneity and multi-tenant imbalance. Co-locating latency-sensitive real-time services with background throughput tenants reflects actual production Kubernetes cluster dynamics.
- **Trace Fidelity Note**: This workload's heterogeneous traffic profile is **MODELED AFTER** the imbalance and co-location characteristics reported in the Alibaba cluster trace papers, rather than a direct bit-for-bit replay of the raw trace data.

## 2. One-Line Success Criteria
> **Expected Finding**: Under heterogeneous multi-tenant load, `qos_dynamic` dynamically balances rate-limiting per tenant based on observed lock contention, whereas static `qos_tiered`'s fixed tier assignment causes latency mismatches for co-located tenants.

## 3. Workload Parameters
- **Topology**: Reuses existing 3-victim data plane (`setup_topology.sh`).
- **Tenant Profile 1 (Victim 1 - 10.0.0.10)**: Latency-sensitive real-time service (50 QPS).
- **Tenant Profile 2 (Victim 2 - 10.0.0.11)**: Background throughput service (200 QPS).
- **Attacker Profile**: Co-located noise targeting Victim 1 (`u20`) and Victim 2 (`u200`) simultaneously.
- **Key Output**: Per-Tenant P99 latency comparison bar chart (`qos_tiered` vs `qos_dynamic`).
- **Architectures Tested**: `qos_tiered`, `qos_dynamic`
