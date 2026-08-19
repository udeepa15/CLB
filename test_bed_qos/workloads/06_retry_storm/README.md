# Workload 06: Unbounded Retry Storm Amplification

## 1. Literature Citation & Methodological Rationale
- **Citations**:
  - Tavori et al., *"Retry Storms: Amplification & Mitigation"* (arXiv, 2025).
  - Industry Engineering Writeups: **DoorDash Engineering** & **Agoda Tech** writeups on cascading microservice retry amplification.
- **Rationale**: When microservice dependencies experience transient slowdowns, un-backoff client retries multiply load exponentially ($2^{K-1} \times N$ amplification factor). This workload tests whether rate-limiting control planes contain request amplification or merely absorb latency.

## 2. One-Line Success Criteria
> **Expected Finding**: Under cascading client retry storms, `qos_dynamic` clamps down rate limits to cap total request amplification hitting application worker threads, whereas static `qos_tiered` allows amplified retry requests to saturate application queues.

## 3. Workload Parameters
- **Server Injection**: 20% failure/delay rate (HTTP 503 / 100ms stall) via `victim_server_retry.py`.
- **Attacker Profile**: Concurrent background flood (`u20`).
- **Key Output**: Request Amplification Factor (Total Hits vs Intended Target QPS) and P99 Latency.
- **Architectures Tested**: `qos_tiered`, `qos_dynamic`
