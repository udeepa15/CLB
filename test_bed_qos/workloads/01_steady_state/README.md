# Workload 01: Extended 60s Steady-State Control Baseline

## 1. Literature Citation & Methodological Rationale
- **Citation**: Gan et al., *"An Open-Source Benchmark Suite for Microservices and Their Hardware-Software Implications for Cloud & Edge Systems"* (ASPLOS 2019, **DeathStarBench**).
- **Rationale**: DeathStarBench establishes constant-throughput Fortio/wrk2 load generation as the baseline methodology for evaluating microservice network control planes. Extending trial duration to 60 seconds (vs 10s short samples) verifies true steady-state convergence without transient warmup artifacts.

## 2. One-Line Success Criteria
> **Expected Finding**: Under constant, non-oscillating flood conditions, `qos_tiered` (static priority tiering) and `qos_dynamic` (adaptive Stackelberg control) perform **SIMILARLY**, as static tiering is specifically optimized for fixed workload profiles.

## 3. Workload Parameters
- **Duration**: 60 seconds per trial
- **Victim Load**: Constant 50 QPS, 2 TCP connections
- **Attacker Profile**: Constant UDP flood across levels (`0`, `u200`, `u20`, `u2`, `u1`, `flood`)
- **Architectures Tested**: `qos_tiered`, `qos_dynamic`
