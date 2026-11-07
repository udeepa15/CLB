#!/usr/bin/env python3
"""analyze_density.py - analyse a container-density run (run_density.py).

Usage: python3 analyze_density.py [results/density_<ts>]   (default: newest density run)

Outputs (in the run directory):
  density_summary.csv         median + 95% bootstrap CI of every metric per (scenario, arm, N)
  density_slopes.csv          Theil-Sen slope of each metric vs N per (scenario, arm), stratified bootstrap CI,
                              and for the primary metric (pooled P99) the slope difference vs qos_open with a
                              Bonferroni-adjusted CI (significant = CI excludes 0)
  density_damage.csv          attack damage = P99(attack) / P99(none) at the same N, with bootstrap CI
  density_identification.csv  precision / recall / false-positive tenants / time-to-classify vs N (auto arms)
  density_setup.csv           topology and container start-up cost vs N
  plots/*.png                 metric-vs-N line charts (one per scenario), damage, overheads, identification

Trials whose floods were not all verified (flood_ok = False) are excluded.
"""
import glob
import json
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "shared"))
from classify_metrics import classification_metrics  # noqa: E402

RNG = np.random.default_rng(20261106)
N_BOOT = 1000
PRIMARY = "p99_ms"
METRICS = ["p99_ms", "p999_ms", "worst_client_p99_ms", "p99_bystander_ms", "p99_targeted_ms", "errors",
           "cpu_busy_tenants", "cpu_softirq_tenants", "ctrl_tick_ms_p99", "prog_ns_mean", "attach_s", "ingress_pps"]
LABELS = {"p99_ms": "pooled victim P99 (ms)", "p999_ms": "pooled victim P99.9 (ms)",
          "worst_client_p99_ms": "worst client P99 (ms)", "cpu_busy_tenants": "tenant CPU busy (fraction)",
          "ctrl_tick_ms_p99": "controller tick p99 (ms)", "prog_ns_mean": "eBPF run time per packet (ns)",
          "attach_s": "attach time (s)", "damage": "P99 under attack / P99 without (x)"}

# Fixed arm -> colour/marker (colour follows the entity, never its rank). Reference categorical palette
# (light surface), slots in order; markers are the secondary encoding for CVD readers and print.
ARM_ORDER = ["sidecarless", "qos_open", "qos_static", "qos_dynamic_fixed", "qos_auto_port", "qos_markov",
             "qos_tc_police", "qos_tiered"]
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
MARKERS = ["o", "s", "^", "D", "v", "P", "X", "*"]
SURFACE, INK, INK2 = "#fcfcfb", "#1f1e1d", "#52514e"


def style(arm):
    i = ARM_ORDER.index(arm) if arm in ARM_ORDER else len(ARM_ORDER) - 1
    return PALETTE[i], MARKERS[i]


# ---------------------------------------------------------------- statistics
def boot_median_ci(v):
    v = np.asarray(v, float)
    v = v[~np.isnan(v)]
    if len(v) == 0:
        return np.nan, np.nan, np.nan
    if len(v) == 1:
        return float(v[0]), np.nan, np.nan
    b = np.median(RNG.choice(v, (N_BOOT, len(v)), replace=True), axis=1)
    return float(np.median(v)), float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))


def theil_sen(x, y):
    i, j = np.triu_indices(len(x), 1)
    dx = x[j] - x[i]
    m = dx != 0
    return float(np.median((y[j] - y[i])[m] / dx[m])) if m.any() else np.nan


def boot_slopes(x, y):
    """Stratified bootstrap: resample the trials within each N level, recompute the Theil-Sen slope."""
    groups = [np.flatnonzero(x == lv) for lv in np.unique(x)]
    out = np.empty(N_BOOT)
    for b in range(N_BOOT):
        idx = np.concatenate([RNG.choice(g, len(g), replace=True) for g in groups])
        out[b] = theil_sen(x[idx], y[idx])
    return out


# ---------------------------------------------------------------- loading
def latest_run():
    runs = [d for d in glob.glob(os.path.join(HERE, "results", "density_*")) if os.path.isdir(d)]
    if not runs:
        sys.exit("no density runs found")
    return max(runs, key=os.path.getmtime)


def load(run):
    frames, setup = [], []
    for d in sorted(glob.glob(os.path.join(run, "N*"))):
        if "_partial_" in d or not os.path.exists(os.path.join(d, "results_summary_raw.csv")):
            continue
        df = pd.read_csv(os.path.join(d, "results_summary_raw.csv"))
        df["level_dir"] = d
        frames.append(df)
        if os.path.exists(os.path.join(d, "setup.json")):
            s = json.load(open(os.path.join(d, "setup.json")))
            setup.append({"N": int(df["n_victims"].iloc[0]) if len(df) else None, **s})
    if not frames:
        sys.exit(f"no completed levels in {run}")
    df = pd.concat(frames, ignore_index=True)
    df["N"] = df["n_victims"].astype(int)
    return df, pd.DataFrame(setup)


# ---------------------------------------------------------------- tables
def summary_table(df):
    rows = []
    for (sc, arm, n), g in df.groupby(["scenario", "arch", "N"]):
        for m in METRICS:
            if m not in g or g[m].isna().all():
                continue
            med, lo, hi = boot_median_ci(g[m])
            rows.append({"scenario": sc, "arm": arm, "N": n, "metric": m, "n": int(g[m].notna().sum()),
                         "median": med, "ci95_lo": lo, "ci95_hi": hi})
    return pd.DataFrame(rows)


def slope_table(df):
    rows, boots = [], {}
    for (sc, arm), g in df.groupby(["scenario", "arch"]):
        for m in METRICS:
            if m not in g:
                continue
            gg = g[["N", m]].dropna()
            if gg["N"].nunique() < 3:
                continue
            x, y = gg["N"].to_numpy(float), gg[m].to_numpy(float)
            b = boot_slopes(x, y)
            boots[(sc, arm, m)] = b
            rows.append({"scenario": sc, "arm": arm, "metric": m, "levels": int(gg["N"].nunique()), "trials": len(gg),
                         "slope_per_10": 10 * theil_sen(x, y), "ci95_lo_per_10": 10 * np.nanpercentile(b, 2.5),
                         "ci95_hi_per_10": 10 * np.nanpercentile(b, 97.5)})
    st = pd.DataFrame(rows)
    # primary metric: slope difference vs qos_open, Bonferroni over every (scenario, arm) comparison
    comps = [(sc, arm) for (sc, arm, m) in boots if m == PRIMARY and arm != "qos_open" and (sc, "qos_open", m) in boots]
    alpha = 0.05 / max(1, len(comps))
    diff_rows = []
    for sc, arm in comps:
        d = boots[(sc, arm, PRIMARY)] - boots[(sc, "qos_open", PRIMARY)]
        lo, hi = np.nanpercentile(d, 100 * alpha / 2), np.nanpercentile(d, 100 * (1 - alpha / 2))
        diff_rows.append({"scenario": sc, "arm": arm, "metric": PRIMARY, "diff_vs_open_per_10": 10 * float(np.nanmedian(d)),
                          "adj_ci_lo_per_10": 10 * lo, "adj_ci_hi_per_10": 10 * hi, "alpha_bonferroni": alpha,
                          "significant": bool(lo > 0 or hi < 0)})
    return st, pd.DataFrame(diff_rows)


def damage_table(df):
    rows = []
    base = {k: g[PRIMARY].dropna().to_numpy(float) for k, g in df[df.scenario == "none"].groupby(["arch", "N"])}
    for (sc, arm, n), g in df[df.scenario != "none"].groupby(["scenario", "arch", "N"]):
        a, b = g[PRIMARY].dropna().to_numpy(float), base.get((arm, n))
        if b is None or len(a) == 0 or len(b) == 0:
            continue
        r = np.median(a) / np.median(b)
        if len(a) > 1 and len(b) > 1:
            bs = np.median(RNG.choice(a, (N_BOOT, len(a))), axis=1) / np.median(RNG.choice(b, (N_BOOT, len(b))), axis=1)
            lo, hi = np.percentile(bs, [2.5, 97.5])
        else:
            lo = hi = np.nan
        rows.append({"scenario": sc, "arm": arm, "N": n, "damage": r, "ci95_lo": lo, "ci95_hi": hi})
    return pd.DataFrame(rows)


def identification_table(df):
    rows = []
    for _, r in df[df["arch"].str.startswith("qos_auto")].iterrows():
        tdir = os.path.join(r["level_dir"], "raw", f"{r['arch']}_{r['scenario']}_rep_{r['rep']}")
        try:
            truth = json.load(open(os.path.join(tdir, "truth.json")))
        except OSError:
            continue
        labels = truth["attacker_port_labels"] if r["arch"].endswith("port") else truth["attacker_ip_labels"]
        m = classification_metrics(os.path.join(tdir, "controller.jsonl"), labels, truth["start_epoch"])
        rows.append({"arm": r["arch"], "scenario": r["scenario"], "N": r["N"], "rep": r["rep"], **m})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- plots
def _axes_style(ax, ylabel):
    ax.set_facecolor(SURFACE)
    ax.grid(True, color=INK2, alpha=0.18, linewidth=0.6)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(INK2)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.set_xlabel("victim containers (N)", color=INK, fontsize=9)
    ax.set_ylabel(ylabel, color=INK, fontsize=9)
    ax.set_ylim(bottom=0)


def line_plot(summ, metric, scenario, path, title, exclude=()):
    s = summ[(summ.metric == metric) & (summ.scenario == scenario) & ~summ.arm.isin(exclude)]
    if s.empty:
        return
    fig, ax = plt.subplots(figsize=(6.4, 3.8), facecolor=SURFACE)
    for arm in [a for a in ARM_ORDER if a in set(s.arm)]:
        g = s[s.arm == arm].sort_values("N")
        c, mk = style(arm)
        ax.fill_between(g.N, g.ci95_lo, g.ci95_hi, color=c, alpha=0.14, linewidth=0)
        ax.plot(g.N, g["median"], color=c, marker=mk, markersize=5.5, linewidth=1.6, label=arm,
                markeredgecolor=SURFACE, markeredgewidth=0.8)
    _axes_style(ax, LABELS.get(metric, metric))
    ax.set_title(title, color=INK, fontsize=10, loc="left")
    ax.legend(frameon=False, fontsize=8, labelcolor=INK, ncol=2)
    fig.tight_layout()
    fig.savefig(path, dpi=200, facecolor=SURFACE)
    plt.close(fig)


def damage_plot(dmg, path):
    scen = sorted(dmg.scenario.unique())
    if not scen:
        return
    fig, axes = plt.subplots(1, len(scen), figsize=(5.2 * len(scen), 3.8), facecolor=SURFACE, squeeze=False)
    for ax, sc in zip(axes[0], scen):
        s = dmg[dmg.scenario == sc]
        for arm in [a for a in ARM_ORDER if a in set(s.arm)]:
            g = s[s.arm == arm].sort_values("N")
            c, mk = style(arm)
            ax.fill_between(g.N, g.ci95_lo, g.ci95_hi, color=c, alpha=0.14, linewidth=0)
            ax.plot(g.N, g.damage, color=c, marker=mk, markersize=5.5, linewidth=1.6, label=arm,
                    markeredgecolor=SURFACE, markeredgewidth=0.8)
        ax.axhline(1.0, color=INK2, linewidth=0.8, alpha=0.6)
        _axes_style(ax, LABELS["damage"])
        ax.set_title(f"attack damage, {sc}", color=INK, fontsize=10, loc="left")
    axes[0][0].legend(frameon=False, fontsize=8, labelcolor=INK, ncol=2)
    fig.tight_layout()
    fig.savefig(path, dpi=200, facecolor=SURFACE)
    plt.close(fig)


def overhead_plot(summ, path):
    """Small multiples (one axis each, never a dual axis): controller tick, eBPF cost, CPU, attach time."""
    panels = [m for m in ("ctrl_tick_ms_p99", "prog_ns_mean", "cpu_busy_tenants", "attach_s")
              if not summ[(summ.metric == m) & (summ.scenario == "none")].empty]
    if not panels:
        return
    fig, axes = plt.subplots(1, len(panels), figsize=(4.2 * len(panels), 3.4), facecolor=SURFACE, squeeze=False)
    for ax, m in zip(axes[0], panels):
        s = summ[(summ.metric == m) & (summ.scenario == "none")]
        for arm in [a for a in ARM_ORDER if a in set(s.arm)]:
            g = s[s.arm == arm].sort_values("N")
            c, mk = style(arm)
            ax.plot(g.N, g["median"], color=c, marker=mk, markersize=5, linewidth=1.6, label=arm,
                    markeredgecolor=SURFACE, markeredgewidth=0.8)
        _axes_style(ax, LABELS.get(m, m))
        ax.set_title(f"{LABELS.get(m, m)} (no attack)", color=INK, fontsize=9, loc="left")
    axes[0][0].legend(frameon=False, fontsize=7, labelcolor=INK)
    fig.tight_layout()
    fig.savefig(path, dpi=200, facecolor=SURFACE)
    plt.close(fig)


# ---------------------------------------------------------------- main
def main():
    run = sys.argv[1] if len(sys.argv) > 1 else latest_run()
    df, setup = load(run)
    bad = df[df["flood_ok"] != True]  # noqa: E712
    if len(bad):
        print(f"excluding {len(bad)} trials with unverified floods")
    df = df[df["flood_ok"] == True].copy()  # noqa: E712
    df.loc[df["N"] < 20, "p999_ms"] = np.nan      # < 12,000 requests per trial: P99.9 not reportable
    print(f"{run}\n{len(df)} trials; levels N={sorted(df.N.unique())}; arms={sorted(df.arch.unique())}; "
          f"scenarios={sorted(df.scenario.unique())}")

    summ = summary_table(df)
    summ.to_csv(os.path.join(run, "density_summary.csv"), index=False)
    slopes, diffs = slope_table(df)
    slopes.to_csv(os.path.join(run, "density_slopes.csv"), index=False)
    if not diffs.empty:
        diffs.to_csv(os.path.join(run, "density_slope_vs_open.csv"), index=False)
    dmg = damage_table(df)
    dmg.to_csv(os.path.join(run, "density_damage.csv"), index=False)
    ident = identification_table(df)
    if not ident.empty:
        ident.to_csv(os.path.join(run, "density_identification.csv"), index=False)
    if not setup.empty:
        setup.sort_values("N").to_csv(os.path.join(run, "density_setup.csv"), index=False)

    pd.set_option("display.width", 220)
    print("\n--- pooled victim P99 (ms), median per N ---")
    p = summ[summ.metric == PRIMARY].pivot_table(index=["scenario", "arm"], columns="N", values="median")
    print(p.round(2).to_string())
    print("\n--- slope of pooled P99 vs N (ms per +10 containers, Theil-Sen, 95% CI) ---")
    s = slopes[slopes.metric == PRIMARY][["scenario", "arm", "slope_per_10", "ci95_lo_per_10", "ci95_hi_per_10"]]
    print(s.round(3).to_string(index=False))
    if not diffs.empty:
        print("\n--- slope difference vs qos_open (Bonferroni-adjusted CI) ---")
        print(diffs.round(3).to_string(index=False))
    if not dmg.empty:
        print("\n--- attack damage P99(attack)/P99(none) ---")
        print(dmg.pivot_table(index=["scenario", "arm"], columns="N", values="damage").round(2).to_string())
    if not ident.empty:
        print("\n--- identification vs N (median over reps) ---")
        print(ident.groupby(["scenario", "N"])[["precision", "recall", "fp_tenants", "time_to_classify_s"]]
              .median().round(3).to_string())
    if not setup.empty:
        print("\n--- setup cost vs N ---")
        print(setup.sort_values("N").to_string(index=False))

    out = os.path.join(run, "plots")
    os.makedirs(out, exist_ok=True)
    for sc in sorted(df.scenario.unique()):
        for m in ("p99_ms", "p999_ms", "worst_client_p99_ms"):
            line_plot(summ, m, sc, os.path.join(out, f"{m}_{sc}.png"), f"{LABELS[m]} vs density, scenario {sc}")
            # sidecarless reaches hundreds of ms and flattens every other arm: same chart without it, own scale
            line_plot(summ, m, sc, os.path.join(out, f"{m}_{sc}_qos_arms.png"),
                      f"{LABELS[m]} vs density, {sc} (without sidecarless)", exclude=("sidecarless",))
    damage_plot(dmg, os.path.join(out, "damage.png"))
    overhead_plot(summ, os.path.join(out, "overheads.png"))
    print(f"\nplots: {out}")


if __name__ == "__main__":
    main()
