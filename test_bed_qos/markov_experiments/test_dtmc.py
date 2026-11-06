#!/usr/bin/env python3
"""Offline tests for DTMCPredictor forgetting (no root / eBPF needed): python3 test_dtmc.py"""
import os, random, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qos_controller_markov import DTMCPredictor, STATE_LOW, STATE_SEVERE


class Original:  # verbatim copy of the pre-change model
    def __init__(self, alpha=0.1):
        self.alpha, self.counts, self.last = alpha, [[0] * 4 for _ in range(4)], None
    def update(self, s):
        if self.last is not None: self.counts[self.last][s] += 1
        self.last = s
    def matrix(self):
        return [[(self.counts[i][j] + self.alpha) / (sum(self.counts[i]) + 4 * self.alpha) for j in range(4)] for i in range(4)]


def test_default_matches_original():
    rng = random.Random(1); a, b = Original(), DTMCPredictor()
    for _ in range(500):
        s = rng.randrange(4); a.update(s); b.update(s)
    for r1, r2 in zip(a.matrix(), b.get_transition_matrix()):
        assert all(abs(x - y) < 1e-12 for x, y in zip(r1, r2))


def test_window_matches_recount():
    rng = random.Random(2); W = 25; p = DTMCPredictor(window=W); seq = []
    for _ in range(300):
        s = rng.randrange(4); p.update(s); seq.append(s)
    pairs = list(zip(seq[:-1], seq[1:]))[-W:]
    ref = [[0.0] * 4 for _ in range(4)]
    for i, j in pairs: ref[i][j] += 1
    assert ref == p.counts and abs(p.effective_samples() - W) < 1e-9


def test_invalid_args():
    for kw in ({"forget_lambda": 0.0}, {"forget_lambda": 1.5}, {"window": 0}, {"window": 5, "forget_lambda": 0.9}):
        try: DTMCPredictor(**kw)
        except ValueError: continue
        raise AssertionError(f"accepted invalid {kw}")


def ticks_to_adapt(make, calm_ticks=300, limit=2000, thr=0.6):
    """Regime A: calm (LOW always). Regime B: LOW->SEVERE->LOW... Ticks after the switch until
    P(HIGH|SEVERE from LOW) >= thr, i.e. the model predicts escalation from LOW."""
    p = make()
    for _ in range(calm_ticks): p.update(STATE_LOW)
    for t in range(1, limit):
        p.update(STATE_SEVERE if t % 2 else STATE_LOW)
        if p.last_state == STATE_LOW and p.predict_high_severe_prob(STATE_LOW) >= thr:
            return t
    return None


def test_forgetting_adapts_faster():
    never = ticks_to_adapt(lambda: DTMCPredictor())
    res = {"never": never, "lambda=0.99": ticks_to_adapt(lambda: DTMCPredictor(forget_lambda=0.99)),
           "lambda=0.95": ticks_to_adapt(lambda: DTMCPredictor(forget_lambda=0.95)),
           "lambda=0.90": ticks_to_adapt(lambda: DTMCPredictor(forget_lambda=0.90)),
           "window=50": ticks_to_adapt(lambda: DTMCPredictor(window=50)),
           "window=20": ticks_to_adapt(lambda: DTMCPredictor(window=20))}
    print("ticks to adapt after regime change:", res)
    assert never is not None and all(v is not None and v < never for k, v in res.items() if k != "never")
    assert res["lambda=0.90"] <= res["lambda=0.95"] <= res["lambda=0.99"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"): fn(); print("PASS", name)
