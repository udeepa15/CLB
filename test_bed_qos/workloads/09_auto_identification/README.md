# Workload 09 - automatic attacker/victim identification

Arms: `qos_tiered` (static), `qos_dynamic_fixed` (hardcoded IP list), `qos_auto_ip` (fair share, IP identity),
`qos_auto_port` (fair share, ingress-port identity). The auto arms are configured with NO victim/attacker address.

| scenario | what it tests | expectation |
|---|---|---|
| single | baseline flooder | all identify it |
| two_attackers | a second flooder that the hardcoded list trusts (victim3 is a "victim" there) | hardcoded list misses it; auto arms catch both |
| spoof | `hping3 --rand-source` | IP identity fragments across thousands of sources; port identity holds |
| benign_heavy | no attacker, one busy legitimate tenant | nothing capped (any flagged key is a false positive) |
| heavy_victim | victim itself at 500 QPS under attack | victim not capped unless above its fair share |

Metrics per trial (from the controller log, ground truth in `raw/truth_*.json`): precision, recall,
time-to-classify, false-positive ticks; plus victim P99. These expectations are hypotheses, not results.

Known limits to state in the report: `--capacity-pps` is a constant until Phase 5 calibrates it; a legitimate
tenant above its fair share during overload IS capped (fairness, not exemption); existing IP-keyed arms charge a
packet at every hook it crosses (ingress and egress), the port limiter charges once, so caps are not byte-identical
between `qos_auto_ip` and `qos_auto_port`.

Run: `sudo python3 ../../smoke_test.py --arms qos_auto_ip qos_auto_port && sudo bash ../run_workload_09.sh 10`
