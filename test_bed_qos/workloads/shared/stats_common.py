#!/usr/bin/env python3
"""stats_common.py - shared, dependency-light (numpy/pandas) statistics for all workloads.

report(df, metrics, group_cols, out_csv) produces, for every metric x condition group:
  * per-arm n, median, IQR and a bootstrap 95% CI of the median
  * all pairwise arm comparisons: Mann-Whitney U (tie-corrected, continuity-corrected
    normal approximation), Cliff's delta (effect size), Hodges-Lehmann median shift
  * Bonferroni-adjusted significance across the WHOLE family of tests in the call

Notes for the report: with n=10 per cell the normal approximation is adequate but not
exact; flag borderline p-values. Rows with n<3 are reported but never called significant.
"""
import itertools
import math

import numpy as np
import pandas as pd

RNG_SEED = 12345
N_BOOT = 5000


def mann_whitney_p(x, y):
    """Two-sided p-value, tie-corrected sigma, continuity correction."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    n1, n2 = len(x), len(y)
    if n1 < 1 or n2 < 1:
        return float("nan"), float("nan")
    allv = np.concatenate([x, y])
    ranks = pd.Series(allv).rank(method="average").to_numpy()
    r1 = ranks[:n1].sum()
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u = min(u1, n1 * n2 - u1)
    n = n1 + n2
    _, counts = np.unique(allv, return_counts=True)
    tie = (counts ** 3 - counts).sum()
    sigma2 = n1 * n2 / 12.0 * ((n + 1) - tie / (n * (n - 1))) if n > 1 else 0.0
    if sigma2 <= 0:
        return u, 1.0
    z = (abs(u - n1 * n2 / 2.0) - 0.5) / math.sqrt(sigma2)
    return u, min(1.0, max(0.0, math.erfc(max(z, 0.0) / math.sqrt(2))))


def cliffs_delta(x, y):
    """P(x>y) - P(x<y); +1 means x always larger. Magnitude: <.147 negl, <.33 small, <.474 med, else large."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) == 0 or len(y) == 0:
        return float("nan")
    diff = x[:, None] - y[None, :]
    return float(((diff > 0).sum() - (diff < 0).sum()) / diff.size)


def hodges_lehmann(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) == 0 or len(y) == 0:
        return float("nan")
    return float(np.median((x[:, None] - y[None, :]).ravel()))


def boot_ci_median(x, n_boot=N_BOOT, seed=RNG_SEED):
    x = np.asarray(x, float)
    if len(x) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    meds = np.median(rng.choice(x, size=(n_boot, len(x)), replace=True), axis=1)
    return float(np.percentile(meds, 2.5)), float(np.percentile(meds, 97.5))


def report(df, metrics, group_cols=None, out_csv=None, arm_col="arch", alpha=0.05, verbose=True):
    group_cols = list(group_cols or [])
    groups = [((), df)] if not group_cols else [
        (k if isinstance(k, tuple) else (k,), g) for k, g in df.groupby(group_cols, sort=True)]
    summary_rows, pair_rows = [], []
    for metric in metrics:
        for key, g in groups:
            cond = dict(zip(group_cols, key))
            arms = sorted(g[arm_col].unique())
            vals = {a: g.loc[g[arm_col] == a, metric].dropna().to_numpy(float) for a in arms}
            for a in arms:
                v = vals[a]
                lo, hi = boot_ci_median(v)
                summary_rows.append({"metric": metric, **cond, "arm": a, "n": len(v),
                                     "median": float(np.median(v)) if len(v) else float("nan"),
                                     "q25": float(np.percentile(v, 25)) if len(v) else float("nan"),
                                     "q75": float(np.percentile(v, 75)) if len(v) else float("nan"),
                                     "ci95_lo": lo, "ci95_hi": hi})
            for a, b in itertools.combinations(arms, 2):
                u, p = mann_whitney_p(vals[a], vals[b])
                pair_rows.append({"metric": metric, **cond, "arm_a": a, "arm_b": b,
                                  "n_a": len(vals[a]), "n_b": len(vals[b]),
                                  "median_diff_a_minus_b": hodges_lehmann(vals[a], vals[b]),
                                  "cliffs_delta": cliffs_delta(vals[a], vals[b]), "U": u, "p": p})
    pairs = pd.DataFrame(pair_rows)
    summ = pd.DataFrame(summary_rows)
    if not pairs.empty:
        m = int(pairs["p"].notna().sum())
        pairs["alpha_bonferroni"] = alpha / max(m, 1)
        ok = (pairs["n_a"] >= 3) & (pairs["n_b"] >= 3) & (pairs["p"] < pairs["alpha_bonferroni"])
        pairs["significant"] = ok
        pairs["family_size"] = m
    if out_csv:
        base = out_csv[:-4] if out_csv.endswith(".csv") else out_csv
        summ.to_csv(base + "_summary.csv", index=False)
        pairs.to_csv(base + "_pairwise.csv", index=False)
    if verbose:
        pd.set_option("display.width", 200, "display.max_columns", 30)
        print("\n--- Per-arm medians (95% bootstrap CI) ---")
        print(summ.round(3).to_string(index=False))
        if not pairs.empty:
            print(f"\n--- Pairwise (Bonferroni alpha = {pairs['alpha_bonferroni'].iloc[0]:.5f}, family={pairs['family_size'].iloc[0]}) ---")
            print(pairs.round(4).to_string(index=False))
    return summ, pairs
