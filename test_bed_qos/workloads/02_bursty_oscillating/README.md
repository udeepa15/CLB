# Workload 02: Bursty Oscillating Attacker Traffic

## 1. Literature Citation & Methodological Rationale
- **Citations**:
  - de Paula Jr. et al., *"Handling Flash-Crowd Events to Improve the Performance of Web Applications"* (2014).
  - *"Detecting and Handling Flash-Crowd Events on Cloud Environments"* (arXiv 1510.03913, using the WorldCup98 trace as canonical flash-crowd reference).
- **Rationale**: Real-world attacker behavior rarely follows flat, static rates. Oscillating traffic bursts test whether adaptive rate limiters react quickly without under-reacting or oscillating unstable.

## 2. One-Line Success Criteria
> **Expected Finding**: `qos_dynamic`'s real-time feedback loop rapidly clamps down on high-contention burst phases (e.g. `flood` for 5s) and relaxes during low phases (`u200` for 5s), maintaining low victim tail latency throughout burst transitions.

## 3. Workload Parameters
- **Oscillation Pattern**: Alternates between `u200` (low) and `flood` (max) every 5 seconds.
- **Victim Load**: Constant 50 QPS.
- **Key Output**: Time-series timeline plot showing latency over run duration.
- **Architectures Tested**: `qos_tiered`, `qos_dynamic`
