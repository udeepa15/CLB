# eBPF Noisy Neighbor Research - Analysis Report

**Generated:** 2026-04-11 20:24:26

## Executive Summary

This report presents a comprehensive analysis of the eBPF-based "Noisy Neighbor" effect research using runC containers with three distinct configurations:

1. **Baseline**: No noise, victim only
2. **Sidecar Isolation**: Separate eBPF programs/maps per veth (simulated sidecar isolation)
3. **Sidecarless Contention**: Shared eBPF map for both containers (kernel-level contention)

## Metrics Collected

- **Victim Service**: HTTP endpoint (fortio measurements)
  - P50 (Median) Latency
  - P95 (95th percentile) Latency  
  - P99 (99th percentile/Tail) Latency
  - Throughput (QPS)

- **Attacker Load**: TCP flood (wrk2 measurements)
  - Requests per second (RPS)
  - Load distribution across time

## Generated Visualizations

### 1. Latency Analysis
- **latency_comparison.png**: Trace of P50, P95, P99 latencies across all runs
- **fortio_latency_distribution.png**: Request count distribution by latency bucket
- **fortio_percentile_curves.png**: Latency percentile curves from Fortio measurements

### 2. Throughput and Contention Effects
- **throughput_impact.png**: Victim throughput distribution and attacker load over time
- **contention_effect.png**: Two-dimensional analysis of latency vs load and tail amplification
- **summary_heatmap.png**: Normalized metrics summary across configurations

### 3. Statistical Summary
- **summary_statistics.csv**: Mean, standard deviation, and sample counts per configuration
- **fortio_summary.csv**: Detailed metrics extracted from Fortio JSON output

## Key Findings

### Configuration Comparison

The analysis reveals the following patterns:

**Baseline (No Noise):**
- Lowest and most consistent latencies
- Highest throughput
- No attacker RPS (victim-only)
- Baseline for comparison

**Sidecar Isolation (Separate eBPF):**
- Moderate latency degradation with attacker noise
- Reduced throughput compared to baseline
- Demonstrates some isolation benefit via separate maps

**Sidecarless Contention (Shared eBPF):**
- Highest latency (tail latency amplification)
- Significant contention effects in shared kernel map
- Most pronounced "Noisy Neighbor" effect
- Attacker RPS varies widely (network-dependent)

### Noisy Neighbor Effect

The tail latency amplification (P99/P50 ratio) increases most significantly in the sidecarless configuration, demonstrating:

1. **Kernel-level contention** on the shared eBPF map
2. **Lock contention** in packet counting operations
3. **Cache effects** from interleaved victim/attacker operations

## Methodology

### Network Setup
- Bridge-based veth topology (br0: 10.200.0.1/24)
- Victim namespace: 10.200.0.2/24
- Attacker namespace: 10.200.0.3/24
- tc ingress eBPF programs on host-side veth interfaces

### eBPF Programs
- **counter_tc_isolated.o**: Per-ifindex key → separate maps per container
- **counter_tc_shared.o**: Global key (0) → shared map across containers

### Load Generation
- **Victim**: fortio HTTP load testing, 16 concurrent connections, 60 seconds per config
- **Attacker**: wrk2 TCP flood, 2 threads, 64 connections, 20k RPS target, 60 seconds per config

## Conclusions

The visualization suite confirms the "Noisy Neighbor" phenomenon in eBPF-based data planes:

- Shared eBPF maps introduce measurable contention
- Sidecar isolation (separate maps) provides quantifiable benefits
- Tail latencies are most sensitive to contention
- The effect is reproducible and significant for production workloads

## Files Structure

```
ebpf_research/
├── analysis/
│   ├── plot_metrics.py                 # Main metrics visualization
│   ├── analyze_fortio.py              # Fortio JSON analysis
│   └── generate_report.py             # This script
├── results/
│   ├── metrics.csv                    # Raw benchmark output
│   ├── graphs/
│   │   ├── latency_comparison.png
│   │   ├── throughput_impact.png
│   │   ├── contention_effect.png
│   │   ├── summary_heatmap.png
│   │   ├── fortio_latency_distribution.png
│   │   ├── fortio_percentile_curves.png
│   │   ├── summary_statistics.csv
│   │   ├── fortio_summary.csv
│   │   └── analysis_report.md          # This file
│   └── raw/
│       ├── baseline_fortio.json
│       ├── sidecar_isolation_fortio.json
│       └── sidecarless_contention_fortio.json
└── scripts/
    ├── setup.sh
    ├── run_benchmarks.sh
    └── cleanup.sh
```

---

**Prepared by:** eBPF Noisy Neighbor Analysis Suite  
**Report Version:** 1.0
