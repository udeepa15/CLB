#!/usr/bin/env python3
"""Offline tests for fairshare.py (no root / eBPF): python3 test_fairshare.py

Includes synthetic traces that mirror the testbed scenarios and report precision, recall and
time-to-classify, plus the expected FAILURE of IP keying under source spoofing and its fix by
port keying (Stage A vs Stage B).
"""
import os
import random
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fairshare import FairShareClassifier, max_min_share

DT = 0.1
CAP = 10_000.0  # pps


def run(trace, **kw):
    """trace: list over ticks of {key: pps}. Returns (classifier, [Decision])."""
    c = FairShareClassifier(CAP, **kw)
    cum, out = {}, []
    for tick in trace:
        for k, pps in tick.items():
            cum[k] = cum.get(k, 0) + int(pps * DT)
        out.append(c.update(dict(cum), DT))
    return c, out


def first_heavy(decisions, key):
    for i, d in enumerate(decisions):
        if key in d.heavy:
            return i
    return None


def test_water_filling():
    assert max_min_share([100, 200], 10_000) == float("inf")           # everyone fits
    assert abs(max_min_share([500, 50_000], 10_000) - 9_500) < 1e-9    # small one satisfied, rest to big
    assert abs(max_min_share([50_000, 50_000], 10_000) - 5_000) < 1e-9
    assert abs(max_min_share([500, 500, 50_000], 10_000) - 9_000) < 1e-9
    assert max_min_share([], 10_000) == float("inf")


def test_single_attacker_found_victims_never_flagged():
    victims = {"v1": 500, "v2": 500, "v3": 300}
    trace = [dict(victims) for _ in range(20)] + [{**victims, "att": 50_000} for _ in range(40)]
    _, dec = run(trace)
    t = first_heavy(dec, "att")
    assert t is not None and t - 20 <= 4, f"slow detection: {t}"
    assert all(d.heavy <= {"att"} for d in dec), "a victim was flagged"
    print(f"    single attacker: time-to-classify = {(t - 20 + 1)} ticks = {(t - 20 + 1) * DT:.1f}s")


def test_calm_traffic_not_throttled():
    trace = [{"v1": 500, "att": 3_000} for _ in range(60)]  # total 3.5k << capacity: work-conserving
    _, dec = run(trace)
    assert all(not d.heavy for d in dec)


def test_two_attackers():
    trace = [{"v1": 500, "a1": 30_000, "a2": 30_000} for _ in range(40)]
    _, dec = run(trace)
    assert dec[-1].heavy == {"a1", "a2"}


def test_flash_crowd_within_share_is_safe_beyond_share_is_flagged():
    # attacker 50k pps; victim surges 500 -> 5000 (== fair share 5000): safe
    trace = [{"v": 500, "att": 50_000} for _ in range(20)] + [{"v": 5_000, "att": 50_000} for _ in range(40)]
    _, dec = run(trace)
    assert "v" not in dec[-1].heavy and "att" in dec[-1].heavy
    # victim surges to 9000 > share(5000)*1.25: flagged. This is by design (fairness, not exemption).
    trace = [{"v": 9_000, "att": 50_000} for _ in range(40)]
    _, dec = run(trace)
    assert "v" in dec[-1].heavy, "documented limitation: heavy legitimate tenant is capped to its share"


def test_release_after_attack_stops_and_no_flapping():
    trace = [{"v": 500, "att": 50_000} for _ in range(30)] + [{"v": 500, "att": 1_000} for _ in range(30)]
    _, dec = run(trace, m_off=5)
    assert "att" in dec[29].heavy and "att" not in dec[-1].heavy
    flips = sum(1 for a, b in zip(dec, dec[1:]) if ("att" in a.heavy) != ("att" in b.heavy))
    assert flips == 2, f"expected exactly on-then-off (2 transitions), got {flips}"


def test_hysteresis_ignores_single_tick_blip():
    trace = [{"v": 500} for _ in range(10)] + [{"v": 500, "att": 50_000}] + [{"v": 500} for _ in range(10)]
    _, dec = run(trace, alpha=1.0, k_on=2)
    assert all(not d.heavy for d in dec)


def test_counter_reset_does_not_produce_negative_or_spike():
    c = FairShareClassifier(CAP, alpha=1.0)
    c.update({"a": 1000}, DT)
    d = c.update({"a": 100}, DT)  # LRU eviction restarted the counter
    assert abs(d.rates.get("a", 0) - 1000) < 1e-6


def test_spoofing_ip_keying_fails_port_keying_holds():
    rng = random.Random(7)
    n_src, total_pps = 3_000, 45_000
    ticks = 40
    ip_trace, port_trace = [], []
    for _ in range(ticks):
        ip_tick = {"10.0.0.10": 500}
        port_tick = {"port_vic": 500}
        per = total_pps / n_src
        for s in range(n_src):                     # each fake source ~15 pps
            ip_tick[f"spoof{s}"] = per
        port_tick["port_att"] = total_pps
        ip_trace.append(ip_tick); port_trace.append(port_tick)
    c_ip, dec_ip = run(ip_trace)
    c_pt, dec_pt = run(port_trace)
    assert dec_ip[-1].heavy == set(), "IP keying unexpectedly caught a 3000-source spoofed flood"
    assert dec_pt[-1].heavy == {"port_att"}, "port keying must catch it"
    print(f"    spoofed flood (45k pps over {n_src} sources): IP keying heavy={len(dec_ip[-1].heavy)}"
          f" (MISSED), port keying heavy={sorted(dec_pt[-1].heavy)}")


def test_precision_recall_over_random_scenarios():
    rng = random.Random(3)
    tp = fp = fn = 0
    for _ in range(200):
        victims = {f"v{i}": rng.uniform(100, 2000) for i in range(rng.randint(1, 4))}
        attackers = {f"a{i}": rng.uniform(15_000, 80_000) for i in range(rng.randint(0, 2))}
        trace = [{**victims, **attackers} for _ in range(30)]
        _, dec = run(trace)
        got = dec[-1].heavy
        tp += len(got & set(attackers)); fp += len(got - set(attackers)); fn += len(set(attackers) - got)
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    print(f"    200 random scenarios: precision={prec:.3f} recall={rec:.3f} (tp={tp} fp={fp} fn={fn})")
    assert prec == 1.0 and rec == 1.0


def test_invalid_params():
    for kw in ({"capacity_pps": 0}, {"capacity_pps": 1, "alpha": 0}, {"capacity_pps": 1, "k_on": 0}):
        try:
            FairShareClassifier(**kw)
        except ValueError:
            continue
        raise AssertionError(kw)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("PASS", name)
