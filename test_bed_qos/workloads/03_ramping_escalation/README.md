# Workload 03: Ramping Escalation Attacker Traffic

## 1. Literature Citation & Methodological Rationale
- **Citation**: Welsh & Culler, *"Adaptive overload control for busy Internet servers"* (USENIX Symposium on Internet Technologies and Systems, 2003).
- **Rationale**: Welsh & Culler established step-ramping load escalation as the standard methodology for measuring control plane reaction time and latency stabilization under monotonically increasing overload.

## 2. One-Line Success Criteria
> **Expected Finding**: Under step-ramping escalation, `qos_dynamic` dynamically scales down the follower's rate limit as contention signals rise, with visually measurable reaction lag, whereas `qos_tiered` maintains a fixed static tier ceiling.

## 3. Workload Parameters
- **Escalation Schedule**: Steps down `hping3 -i` every 2s: `u500` -> `u200` -> `u50` -> `u20` -> `u5` -> `u2` -> `flood`.
- **Victim Load**: Constant 50 QPS.
- **Key Output**: Three-line timeline plot (Attacker Intensity, Controller Rate Limit, Victim P99 Latency).
- **Architectures Tested**: `qos_tiered`, `qos_dynamic`
