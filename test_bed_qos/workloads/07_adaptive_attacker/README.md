# Workload 07: Rational Adaptive Adversary (3-Way Ablation Study)

## 1. Literature Citation & Methodological Rationale
- **Citations**:
  - He et al., *"A Game-Theoretical Approach for Mitigating Edge DDoS Attack"* (IEEE TDSC 2021).
  - *"Review of game theory approaches for DDoS mitigation by SDN"* (2022).
- **Rationale**: Rational, adaptive adversaries actively poll feedback signals (e.g. rate limits, packet drop rates) and dynamically adjust flood intensity to maximize victim disruption while avoiding detection or severe throttling. This workload presents the definitive game-theoretic defense evaluation.

## 2. One-Line Success Criteria
> **Expected Finding**: Under a rational adaptive adversary, `qos_dynamic` (Stackelberg Game Equilibrium) reaches a stable Nash/Stackelberg equilibrium that minimizes victim P99 tail latency, whereas `qos_proportional` oscillates unstably and static `qos_tiered` fails to adapt.

## 3. 3-Way Ablation Architecture Arms
1. **`qos_tiered`**: Static Priority Tiering (Fixed boundary baseline).
2. **`qos_proportional`**: Naive Linear Proportional Feedback Control ($Limit = Baseline - K_p \times HitsPerSec$).
3. **`qos_dynamic`**: Stackelberg Game-Theoretic Leader-Follower Solver.

## 4. Workload Parameters
- **Attacker Profile**: Adaptive Rational Adversary polling controller feedback log every 1s (`AdaptiveAttackerThread`).
- **Victim Load**: Constant 50 QPS.
- **Architectures Tested**: 3 Arms (`qos_tiered`, `qos_proportional`, `qos_dynamic`).
