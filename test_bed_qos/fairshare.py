#!/usr/bin/env python3
"""fairshare.py - identify heavy hitters (attackers) by max-min fair share, with no victim list.

Idea: a tenant is an attacker only if, while the node is overloaded, its measured rate is above
its max-min fair share of the node's capacity. Everyone else is left uncapped by default.

    share  = water level s with  sum_i min(rate_i, s) == capacity   (inf if total <= capacity)
    over_i = rate_i > share * (1 + margin)
    heavy  = over for k_on consecutive ticks; released after m_off consecutive non-over ticks

Properties (tested in test_fairshare.py):
  * work-conserving: if total demand <= capacity nobody is flagged
  * a tenant at or below its fair share is never flagged, however small the others are
  * a tenant ABOVE its fair share during overload IS flagged, even if it is legitimate
    (e.g. a flash crowd bigger than its share). The guarantee is fairness, not exemption.

Pure Python, no kernel or root needed; the controller feeds it cumulative per-key packet counters.
"""
from dataclasses import dataclass, field


def max_min_share(demands, capacity):
    """Water level s such that sum(min(d, s)) == capacity. inf if everyone fits."""
    ds = sorted(d for d in demands if d > 0)
    remaining = float(capacity)
    n = len(ds)
    for i, d in enumerate(ds):
        fair = remaining / (n - i)
        if d <= fair:
            remaining -= d
        else:
            return fair
    return float("inf")


@dataclass
class Decision:
    heavy: set
    share: float
    total_pps: float
    rates: dict = field(default_factory=dict)
    newly_heavy: set = field(default_factory=set)
    released: set = field(default_factory=set)


class FairShareClassifier:
    def __init__(self, capacity_pps, margin=0.25, k_on=2, m_off=5, alpha=0.5, min_pps=50.0):
        """
        capacity_pps: unique packets/s the node sustains without hurting tail latency.
                      (Phase 5 replaces this constant with an auto-calibrated value.)
        margin:       tolerance above the share before a tenant counts as over (avoids flapping
                      on noise right at the share).
        k_on / m_off: hysteresis, in ticks.
        alpha:        EWMA weight of the newest rate sample (1.0 = no smoothing).
        min_pps:      sources slower than this are ignored (they cannot matter, and this keeps
                      thousands of spoofed micro-flows out of the heavy set).
        """
        if capacity_pps <= 0 or not 0 < alpha <= 1 or k_on < 1 or m_off < 1:
            raise ValueError("invalid classifier parameters")
        self.capacity, self.margin = float(capacity_pps), margin
        self.k_on, self.m_off, self.alpha, self.min_pps = k_on, m_off, alpha, min_pps
        self.prev_counts, self.rate = {}, {}
        self.on_ticks, self.off_ticks, self.heavy = {}, {}, set()

    def update(self, counts, dt):
        """counts: {key: cumulative packet count}. dt: seconds since the previous call."""
        if dt <= 0:
            raise ValueError("dt must be > 0")
        for key, cur in counts.items():
            prev = self.prev_counts.get(key, 0)
            delta = cur - prev if cur >= prev else cur   # counter restarted (LRU eviction / map reset)
            inst = delta / dt
            old = self.rate.get(key)
            self.rate[key] = inst if old is None else self.alpha * inst + (1 - self.alpha) * old
        for key in list(self.rate):                      # keys that vanished decay toward zero
            if key not in counts:
                self.rate[key] *= (1 - self.alpha)
                if self.rate[key] < 1.0:
                    del self.rate[key]
        self.prev_counts = dict(counts)

        active = {k: r for k, r in self.rate.items() if r >= self.min_pps}
        total = sum(active.values())
        share = max_min_share(active.values(), self.capacity)
        limit = share * (1 + self.margin)

        newly, released = set(), set()
        for key in set(active) | self.heavy | set(self.on_ticks):
            over = active.get(key, 0.0) > limit
            if over:
                self.on_ticks[key] = self.on_ticks.get(key, 0) + 1
                self.off_ticks[key] = 0
                if key not in self.heavy and self.on_ticks[key] >= self.k_on:
                    self.heavy.add(key)
                    newly.add(key)
            else:
                self.on_ticks[key] = 0
                if key in self.heavy:
                    self.off_ticks[key] = self.off_ticks.get(key, 0) + 1
                    if self.off_ticks[key] >= self.m_off:
                        self.heavy.discard(key)
                        released.add(key)
        for key in [k for k, v in self.on_ticks.items() if v == 0 and k not in self.heavy]:
            del self.on_ticks[key]
        return Decision(set(self.heavy), share, total, dict(active), newly, released)
