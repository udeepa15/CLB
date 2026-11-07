#!/usr/bin/env python3
"""analyze_timevarying.py - analyse a time-varying-attack run (run_timevarying.py).

Usage: python3 analyze_timevarying.py [results/timevarying_<ts>]   (default: newest)

Cells: bursty (whole run), ramp (whole run), regime/calm and regime/attack (measured separately).
Two pre-planned questions, each its own Bonferroni family per metric:
  Q1 protection : does limiting help?            every arm vs qos_open  on victim P99
  Q2 adaptivity : does a controller beat a static cap?
                  every controller arm vs qos_static on victim P99 (protection) AND on attacker goodput
                  (MB/s the attackers got to their targets: efficiency - a static cap throttles even when
                  the traffic is harmless, which is the case adaptive control is meant to win)
Outputs: tv_summary.csv, tv_tests.csv, plots/tradeoff_regime.png, plots/p99_cells.png
"""
import glob
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "shared"))
from stats_common import mann_whitney_p, hodges_lehmann, cliffs_delta  # noqa: E402

RNG = np.random.default_rng(7)
CONTROLLERS = ["qos_dynamic_fixed", "qos_dynamic_adaptive", "qos_markov", "qos_markov_200ms", "qos_markov_l95",
               "qos_auto_port"]
ORDER = ["qos_open", "qos_static", "qos_tc_police"] + CONTROLLERS
# Same hues as the density charts (colour follows the arm); variants reuse their family's hue (no 9th hue)
# and are told apart by marker and by the direct label / row label.
STYLE = {"qos_open": ("#eb6834", "s"), "qos_static": ("#1baf7a", "^"), "qos_tc_police": ("#6250d6", "X"),
         "qos_dynamic_fixed": ("#eda100", "D"), "qos_dynamic_adaptive": ("#eda100", "o"),
         "qos_markov": ("#008300", "P"), "qos_markov_200ms": ("#008300", "h"), "qos_markov_l95": ("#008300", "*"),
         "qos_auto_port": ("#e87ba4", "v")}
SURFACE, INK, INK2 = "#fcfcfb", "#1f1e1d", "#52514e"


def latest():
    runs = [d for d in glob.glob(os.path.join(HERE, "results", "timevarying_*")) if os.path.isdir(d)]
    if not runs:
        sys.exit("no time-varying runs")
    return max(runs, key=os.path.getmtime)


def ci(v):
    v = np.asarray(v, float)
    v = v[~np.isnan(v)]
    if len(v) < 2:
        return (float(v[0]) if len(v) else np.nan), np.nan, np.nan
    b = np.median(RNG.choice(v, (2000, len(v))), axis=1)
    return float(np.median(v)), float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))


def tests(ph, metric, ref, arms_, label):
    rows = []
    for cell, g in ph.groupby("cell"):
        r = g[g.arch == ref][metric].dropna().to_numpy(float)
        for arm in arms_:
            x = g[g.arch == arm][metric].dropna().to_numpy(float)
            if len(x) < 3 or len(r) < 3:
                continue
            _, p = mann_whitney_p(x, r)
            rows.append({"question": label, "metric": metric, "cell": cell, "arm": arm, "ref": ref,
                         "shift_vs_ref": hodges_lehmann(x, r), "cliffs_delta": cliffs_delta(x, r), "p": p,
                         "n": len(x), "n_ref": len(r)})
    t = pd.DataFrame(rows)
    if not t.empty:
        t["family"] = len(t)
        t["alpha_bonferroni"] = 0.05 / len(t)
        t["significant"] = t.p < t.alpha_bonferroni
    return t


def show(t, title, unit):
    if t.empty:
        return
    t = t.copy()
    t["cell_txt"] = t.shift_vs_ref.map(lambda v: f"{v:+.2f}") + t.significant.map({True: "*", False: ""})
    print(f"\n--- {title} ({unit}; * = significant, family {int(t.family.iloc[0])}, alpha {t.alpha_bonferroni.iloc[0]:.4f}) ---")
    print(t.pivot_table(index="arm", columns="cell", values="cell_txt", aggfunc="first").reindex(
        [a for a in ORDER if a in set(t.arm)]).to_string())


def tradeoff_plot(summ, path):
    calm = summ[(summ.cell == "regime/calm")].set_index("arch")
    att = summ[(summ.cell == "regime/attack")].set_index("arch")
    arms_ = [a for a in ORDER if a in calm.index and a in att.index]
    if not arms_:
        return
    fig, ax = plt.subplots(figsize=(6.8, 4.4), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    pts = []
    for a in arms_:
        c, mk = STYLE.get(a, (INK2, "o"))
        x, y = calm.loc[a, "goodput_med"], att.loc[a, "p99_med"]
        ax.errorbar(x, y, xerr=[[x - calm.loc[a, "goodput_lo"]], [calm.loc[a, "goodput_hi"] - x]],
                    yerr=[[y - att.loc[a, "p99_lo"]], [att.loc[a, "p99_hi"] - y]],
                    fmt=mk, color=c, markersize=7, markeredgecolor=SURFACE, markeredgewidth=0.8, elinewidth=1,
                    capsize=0, zorder=3)
        pts.append((a, x, y))
    ax.grid(True, color=INK2, alpha=0.18, linewidth=0.6)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.set_xlabel("calm phase: attacker traffic delivered (MB/s)  →  more efficient", color=INK, fontsize=9)
    ax.set_ylabel("attack phase: victim P99 (ms)  ↓  better protected", color=INK, fontsize=9)
    ax.set_title("Regime change: efficiency in calm vs protection under attack (median, 95% CI)",
                 color=INK, fontsize=9.5, loc="left")
    offered = float(calm["offered_med"].median())
    ax.axvline(offered, color=INK2, linewidth=0.9, linestyle="--", alpha=0.7)
    ax.annotate("offered", (offered, 0), xytext=(-4, 6), textcoords="offset points", ha="right", fontsize=7.5,
                color=INK2)
    xmax = max(offered, float(calm.loc[arms_, "goodput_hi"].max(skipna=True) if calm.loc[arms_, "goodput_hi"].notna().any()
                              else calm.loc[arms_, "goodput_med"].max()))
    ymax = float(np.nanmax(np.r_[att.loc[arms_, "p99_med"].to_numpy(float), att.loc[arms_, "p99_hi"].to_numpy(float)]))
    ax.set_xlim(0, xmax * 1.45)          # room for the direct labels on the right
    ax.set_ylim(0, ymax * 1.12)
    # direct labels: points that share an x (e.g. every controller passes all calm traffic) get their labels
    # spread vertically with a minimum gap, each tied to its point by a thin leader line
    gap = ymax * 1.12 / 20
    groups = {}
    for a, x, y in pts:
        groups.setdefault(round(x / (xmax * 0.04)), []).append((a, x, y))
    for grp in groups.values():
        grp.sort(key=lambda t: t[2])
        placed = []
        for a, x, y in grp:
            ly = max(y, placed[-1] + gap) if placed else y
            placed.append(ly)
        shift = max(0.0, placed[-1] - ymax * 1.08)      # keep the stack inside the axes
        for (a, x, y), ly in zip(grp, placed):
            ly -= shift
            lx = x + xmax * 0.06
            ax.plot([x, lx - xmax * 0.01], [y, ly], color=INK2, linewidth=0.5, alpha=0.6, zorder=2)
            ax.text(lx, ly, a.replace("qos_", ""), fontsize=7.5, color=INK, va="center")
    fig.tight_layout()
    fig.savefig(path, dpi=200, facecolor=SURFACE)
    plt.close(fig)


def cells_plot(summ, path):
    cells = [c for c in ("bursty/all", "ramp/all", "regime/calm", "regime/attack") if c in set(summ.cell)]
    fig, axes = plt.subplots(1, len(cells), figsize=(3.3 * len(cells), 4.0), facecolor=SURFACE, squeeze=False,
                             sharey=True)
    arms_ = [a for a in ORDER if a in set(summ.arch)]
    ypos = {a: i for i, a in enumerate(reversed(arms_))}
    for ax, cell in zip(axes[0], cells):
        ax.set_facecolor(SURFACE)
        s = summ[summ.cell == cell].set_index("arch")
        for a in arms_:
            if a not in s.index:
                continue
            c, mk = STYLE.get(a, (INK2, "o"))
            m, lo, hi = s.loc[a, "p99_med"], s.loc[a, "p99_lo"], s.loc[a, "p99_hi"]
            ax.plot([lo, hi], [ypos[a]] * 2, color=c, linewidth=1.6, alpha=0.7)
            ax.plot(m, ypos[a], mk, color=c, markersize=6.5, markeredgecolor=SURFACE, markeredgewidth=0.8)
        ax.set_title(cell, color=INK, fontsize=9, loc="left")
        ax.grid(True, axis="x", color=INK2, alpha=0.18, linewidth=0.6)
        for sp in ("top", "right", "left"):
            ax.spines[sp].set_visible(False)
        ax.tick_params(colors=INK2, labelsize=8)
        top = np.nanmax(np.r_[s["p99_med"].to_numpy(float), s["p99_hi"].to_numpy(float)])
        ax.set_xlim(0, top * 1.1)
        ax.set_xlabel("victim P99 (ms)", color=INK, fontsize=8.5)
    axes[0][0].set_yticks(list(ypos.values()))
    axes[0][0].set_yticklabels([a.replace("qos_", "") for a in ypos], color=INK, fontsize=8)
    fig.suptitle("Victim P99 under time-varying attacks (median, 95% CI)", color=INK, fontsize=10, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(path, dpi=200, facecolor=SURFACE)
    plt.close(fig)


def main():
    run = sys.argv[1] if len(sys.argv) > 1 else latest()
    ph = pd.read_csv(os.path.join(run, "phases.csv"))
    bad = ph[ph.flood_ok != True]  # noqa: E712
    bad_trials = set(zip(bad.arch, bad.scenario, bad.rep))
    if bad_trials:
        print(f"excluding {len(bad_trials)} trials with an unverified flood step")
    ph = ph[[(a, s, r) not in bad_trials for a, s, r in zip(ph.arch, ph.scenario, ph.rep)]].copy()
    ph["cell"] = ph.scenario + "/" + ph.phase
    print(f"{run}\n{ph[['arch','scenario','rep']].drop_duplicates().shape[0]} trials, "
          f"requests per phase (median) {int(ph.requests.median())}, errors {int(ph.errors.sum())}")

    rows = []
    for (cell, arm), g in ph.groupby(["cell", "arch"]):
        p, plo, phi = ci(g.p99_ms)
        q, qlo, qhi = ci(g.p999_ms)
        gp, glo, ghi = ci(g.to_targets_MBps)
        rows.append({"cell": cell, "arch": arm, "n": len(g), "p99_med": p, "p99_lo": plo, "p99_hi": phi,
                     "p999_med": q, "p999_lo": qlo, "p999_hi": qhi, "goodput_med": gp, "goodput_lo": glo,
                     "goodput_hi": ghi, "offered_med": g.attack_offered_MBps.median(),
                     "cpu_busy_med": g.cpu_busy_tenants.median()})
    summ = pd.DataFrame(rows)
    summ.to_csv(os.path.join(run, "tv_summary.csv"), index=False)
    pd.set_option("display.width", 220)
    print("\n--- victim P99 (ms), median ---")
    print(summ.pivot_table(index="arch", columns="cell", values="p99_med").reindex(
        [a for a in ORDER if a in set(summ.arch)]).round(2).to_string())
    print("\n--- attacker traffic delivered to targets (MB/s), median  [offered in parentheses] ---")
    gp = summ.pivot_table(index="arch", columns="cell", values="goodput_med").round(1)
    off = summ.pivot_table(index="arch", columns="cell", values="offered_med").round(0)
    print((gp.astype(str) + " (" + off.astype(int).astype(str) + ")").reindex(
        [a for a in ORDER if a in set(summ.arch)]).to_string())

    q1 = tests(ph, "p99_ms", "qos_open", [a for a in ORDER if a != "qos_open"], "Q1 protection vs qos_open")
    q2p = tests(ph, "p99_ms", "qos_static", CONTROLLERS, "Q2 adaptivity: P99 vs qos_static")
    q2g = tests(ph, "to_targets_MBps", "qos_static", CONTROLLERS, "Q2 adaptivity: goodput vs qos_static")
    show(q1, "Q1: victim P99 shift vs qos_open", "ms")
    show(q2p, "Q2: victim P99 shift vs qos_static (protection)", "ms")
    show(q2g, "Q2: attacker goodput shift vs qos_static (efficiency)", "MB/s")
    pd.concat([q1, q2p, q2g]).to_csv(os.path.join(run, "tv_tests.csv"), index=False)

    out = os.path.join(run, "plots")
    os.makedirs(out, exist_ok=True)
    tradeoff_plot(summ, os.path.join(out, "tradeoff_regime.png"))
    cells_plot(summ, os.path.join(out, "p99_cells.png"))
    print(f"\nplots: {out}")


if __name__ == "__main__":
    main()
