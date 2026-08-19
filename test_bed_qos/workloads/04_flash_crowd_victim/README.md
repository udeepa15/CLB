# Workload 04: Flash Crowd Victim Demand Surge

## 1. Literature Citation & Methodological Rationale
- **Citations**:
  - de Paula Jr. et al., *"Handling Flash-Crowd Events to Improve the Performance of Web Applications"* (2014).
  - WorldCup98 Trace (canonical flash-crowd reference).
- **Rationale**: Flash crowds represent sudden, legitimate user demand spikes (e.g. breaking news, product launches). Traditional rate limiters often misclassify flash crowds as DDoS attacks and drop legitimate requests. This workload tests that the control plane distinguishes legitimate surges from adversarial floods.

## 2. One-Line Success Criteria
> **Expected Finding**: Under legitimate victim demand surge (50 QPS -> 500 QPS), `qos_dynamic`'s Stackelberg leader-follower model correctly accommodates the leader's growing demand, whereas `qos_tiered` enforces a static upper ceiling regardless of current need.

## 3. Workload Parameters
- **Attacker Profile**: Constant moderate flood (`u20` = 50k pps).
- **Victim Load Profile**: 50 QPS for first 10s, jumping to 500 QPS for second 10s.
- **Key Output**: Victim throughput & P99 latency comparison before and after the flash-crowd surge.
- **Architectures Tested**: `qos_tiered`, `qos_dynamic`
