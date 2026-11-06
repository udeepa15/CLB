# Workload 08 - Attacker regime change

Purpose: test whether an online predictor (DTMC) re-learns when attacker behaviour changes, and
which forgetting mechanism helps (Phase 3 ablation).

- Phase A (30 s): constant low-rate UDP (`u200`, ~5k pps) - calm control.
- Phase B (30 s): sawtooth, `flood` 2 s / `u200` 2 s.
- Victim: fortio 50 QPS, measured separately per phase (`phaseA_*`, `phaseB_*`).
- Default arms: `arms.FORGET_ARMS` (qos_markov, l99, l95, l90, w50, qos_dynamic_fixed).

Hypothesis: after the switch, never-forget `qos_markov` is slowest to predict escalation
(see `markov_experiments/test_dtmc.py`: 898 ticks vs 28 for lambda=0.95 in the synthetic model);
forgetting variants should lower `phaseB_p99_ms`. Report whichever way the data falls.

Run: `sudo bash ../run_workload_08.sh 10`
