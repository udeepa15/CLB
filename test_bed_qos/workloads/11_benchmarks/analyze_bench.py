#!/usr/bin/env python3
"""analyze_bench.py - analyse a workload-11 run. Usage: python3 analyze_bench.py <results dir>

Per (condition, variant) cell:
  * median + 95% bootstrap CI of each metric per arm (bench_summary.csv)
  * latency ADDED over the bare bridge, the way Istio / Linkerd report sidecar overhead (bench_overhead.csv)
  * Family A: every arm vs bare on the primary metric (does it cost / does it protect?)
    Family B: our arms vs envoy_sidecar (are we better than an existing solution on the same machine?)
    Mann-Whitney, Bonferroni within each family (bench_tests.csv)
  * plots/: dot plot of the primary metric per cell; memcached: p99 vs offered load per arm
Trials with unverified floods or a failed app run are excluded.
"""
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

RNG = np.random.default_rng(11)
PRIMARY = {"istio": "p99_ms", "netperf": "p99_us", "memcached": "p99_ms"}
LATENCY = {"istio": ["p50_ms", "p90_ms", "p99_ms", "p999_ms"], "netperf": ["p50_us", "p90_us", "p99_us"],
           "memcached": ["p50_ms", "p99_ms", "p999_ms"]}
ORDER = ["bare", "envoy_sidecar", "envoy_sidecar_mtls", "sidecarless", "qos_open", "qos_static", "qos_markov",
         "qos_dynamic_fixed", "qos_auto_port"]
OURS = ["sidecarless", "qos_open", "qos_static", "qos_markov", "qos_dynamic_fixed", "qos_auto_port"]
STYLE = {"bare": ("#52514e", "o"), "envoy_sidecar": ("#2a78d6", "s"), "envoy_sidecar_mtls": ("#2a78d6", "D"),
         "sidecarless": ("#e34948", "v"), "qos_open": ("#eb6834", "s"), "qos_static": ("#1baf7a", "^"),
         "qos_markov": ("#008300", "P"), "qos_dynamic_fixed": ("#eda100", "D"), "qos_auto_port": ("#e87ba4", "v")}
SURFACE, INK, INK2 = "#fcfcfb", "#1f1e1d", "#52514e"


def ci(v):
    v = np.asarray(v, float)
    v = v[~np.isnan(v)]
    if len(v) < 2:
        return (float(v[0]) if len(v) else np.nan), np.nan, np.nan
    b = np.median(RNG.choice(v, (2000, len(v))), axis=1)
    return float(np.median(v)), float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))


def primary_metric(app, variant):
    if app == "netperf" and variant.startswith("stream"):
        return "throughput"
    return PRIMARY[app]


def family(df, app, ref, arms_, label):
    rows = []
    for (cond, var), g in df.groupby(["condition", "variant"]):
        m = primary_metric(app, var)
        r = g[g.arch == ref][m].dropna().to_numpy(float)
        for arm in arms_:
            x = g[g.arch == arm][m].dropna().to_numpy(float)
            if arm == ref or len(x) < 3 or len(r) < 3:
                continue
            _, p = mann_whitney_p(x, r)
            rows.append({"family": label, "condition": cond, "variant": var, "metric": m, "arm": arm, "ref": ref,
                         "shift_vs_ref": hodges_lehmann(x, r), "cliffs_delta": cliffs_delta(x, r), "p": p})
    t = pd.DataFrame(rows)
    if not t.empty:
        t["family_size"] = len(t)
        t["alpha_bonferroni"] = 0.05 / len(t)
        t["significant"] = t.p < t.alpha_bonferroni
    return t


def show(t, title):
    if t.empty:
        return
    t = t.copy()
    t["cell"] = t.condition + "/" + t.variant
    t["txt"] = t.shift_vs_ref.map(lambda v: f"{v:+.3g}") + t.significant.map({True: "*", False: ""})
    print(f"\n--- {title} (* = significant, family {int(t.family_size.iloc[0])}, alpha {t.alpha_bonferroni.iloc[0]:.4f}) ---")
    print(t.pivot_table(index="arm", columns="cell", values="txt", aggfunc="first").reindex(
        [a for a in ORDER if a in set(t.arm)]).to_string())


def dot_plot(summ, app, path):
    cells = sorted(set(zip(summ.condition, summ.variant)))
    if not cells:
        return
    arms_ = [a for a in ORDER if a in set(summ.arch)]
    ypos = {a: i for i, a in enumerate(reversed(arms_))}
    fig, axes = plt.subplots(1, len(cells), figsize=(3.2 * len(cells), 0.42 * len(arms_) + 1.6), sharey=True,
                             facecolor=SURFACE, squeeze=False)
    for ax, (cond, var) in zip(axes[0], cells):
        m = primary_metric(app, var)
        s = summ[(summ.condition == cond) & (summ.variant == var) & (summ.metric == m)].set_index("arch")
        ax.set_facecolor(SURFACE)
        for a in arms_:
            if a not in s.index:
                continue
            c, mk = STYLE.get(a, (INK2, "o"))
            med, lo, hi = s.loc[a, "median"], s.loc[a, "ci_lo"], s.loc[a, "ci_hi"]
            if pd.notna(lo):
                ax.plot([lo, hi], [ypos[a]] * 2, color=c, linewidth=1.6, alpha=0.75)
            ax.plot(med, ypos[a], mk, color=c, markersize=6.5, markeredgecolor=SURFACE, markeredgewidth=0.8)
        top = float(np.nanmax(np.r_[s["median"].to_numpy(float), s["ci_hi"].to_numpy(float)]))
        ax.set_xlim(0, top * 1.12)
        ax.set_title(f"{cond} / {var}", color=INK, fontsize=9, loc="left")
        ax.set_xlabel(m, color=INK, fontsize=8.5)
        ax.grid(True, axis="x", color=INK2, alpha=0.18, linewidth=0.6)
        for sp in ("top", "right", "left"):
            ax.spines[sp].set_visible(False)
        ax.tick_params(colors=INK2, labelsize=8)
    axes[0][0].set_yticks(list(ypos.values()))
    axes[0][0].set_yticklabels(list(ypos), color=INK, fontsize=8.5)
    fig.suptitle(f"Workload 11 / {app}: primary metric per cell (median, 95% CI)", color=INK, fontsize=10, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(path, dpi=200, facecolor=SURFACE)
    plt.close(fig)


def load_curve(summ, path):
    s = summ[summ.metric == "p99_ms"].copy()
    s["qps"] = s.variant.str.strip("qk").astype(float) * 1000
    conds = sorted(s.condition.unique())
    fig, axes = plt.subplots(1, len(conds), figsize=(5.2 * len(conds), 3.8), facecolor=SURFACE, squeeze=False)
    for ax, cond in zip(axes[0], conds):
        ax.set_facecolor(SURFACE)
        for a in [x for x in ORDER if x in set(s.arch)]:
            g = s[(s.condition == cond) & (s.arch == a)].sort_values("qps")
            c, mk = STYLE.get(a, (INK2, "o"))
            ax.fill_between(g.qps / 1000, g.ci_lo, g.ci_hi, color=c, alpha=0.14, linewidth=0)
            ax.plot(g.qps / 1000, g["median"], color=c, marker=mk, markersize=5.5, linewidth=1.6, label=a,
                    markeredgecolor=SURFACE, markeredgewidth=0.8)
        ax.set_title(f"memcached p99 vs offered load ({cond})", color=INK, fontsize=9.5, loc="left")
        ax.set_xlabel("offered load (k requests/s)", color=INK, fontsize=9)
        ax.set_ylabel("p99 (ms)", color=INK, fontsize=9)
        ax.set_ylim(bottom=0)
        ax.grid(True, color=INK2, alpha=0.18, linewidth=0.6)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
    axes[0][0].legend(frameon=False, fontsize=8, labelcolor=INK)
    fig.tight_layout()
    fig.savefig(path, dpi=200, facecolor=SURFACE)
    plt.close(fig)


def main():
    run = sys.argv[1]
    df = pd.read_csv(os.path.join(run, "results_summary_raw.csv"))
    app = df.app.iloc[0]
    bad = df[(df.flood_ok != True) | (df.app_ok != True)]  # noqa: E712
    if len(bad):
        print(f"excluding {len(bad)} trials (unverified flood or failed app run)")
    df = df[(df.flood_ok == True) & (df.app_ok == True)].copy()  # noqa: E712
    print(f"{run}\napp={app}, {len(df)} trials, arms={sorted(df.arch.unique())}")
    metrics = [m for m in LATENCY[app] + ["throughput", "ops_per_sec", "actual_qps", "errors", "cpu_busy_tenants"]
               if m in df.columns]
    rows = []
    for (cond, var, arch), g in df.groupby(["condition", "variant", "arch"]):
        for m in metrics:
            med, lo, hi = ci(g[m])
            rows.append({"condition": cond, "variant": var, "arch": arch, "metric": m, "n": int(g[m].notna().sum()),
                         "median": med, "ci_lo": lo, "ci_hi": hi})
    summ = pd.DataFrame(rows)
    summ.to_csv(os.path.join(run, "bench_summary.csv"), index=False)
    pd.set_option("display.width", 220)
    for m in metrics:
        if m in ("errors", "cpu_busy_tenants"):
            continue
        p = summ[summ.metric == m].pivot_table(index="arch", columns=["condition", "variant"], values="median")
        if p.notna().any().any():
            print(f"\n--- {m}, median ---")
            print(p.reindex([a for a in ORDER if a in p.index]).round(3).to_string())
    # Istio-style "added latency over bare"
    ov = []
    for (cond, var), g in summ[summ.metric.isin(LATENCY[app])].groupby(["condition", "variant"]):
        base = g[g.arch == "bare"].set_index("metric")["median"]
        for _, r in g[g.arch != "bare"].iterrows():
            if r.metric in base:
                ov.append({"condition": cond, "variant": var, "arch": r.arch, "metric": r.metric,
                           "added_over_bare": r["median"] - base[r.metric]})
    ov = pd.DataFrame(ov)
    if not ov.empty:
        ov.to_csv(os.path.join(run, "bench_overhead.csv"), index=False)
        print("\n--- latency added over bare (median difference; Istio/Linkerd-style overhead) ---")
        print(ov.pivot_table(index="arch", columns=["condition", "variant", "metric"], values="added_over_bare")
              .reindex([a for a in ORDER if a in set(ov.arch)]).round(3).to_string())
    fa = family(df, app, "bare", [a for a in ORDER if a != "bare"], "A: vs bare")
    fb = family(df, app, "envoy_sidecar", [a for a in OURS if a in set(df.arch)], "B: ours vs envoy_sidecar") \
        if "envoy_sidecar" in set(df.arch) else pd.DataFrame()
    show(fa, "Family A: shift vs bare on the primary metric")
    show(fb, "Family B: our arms vs envoy_sidecar on the primary metric")
    pd.concat([fa, fb]).to_csv(os.path.join(run, "bench_tests.csv"), index=False)
    out = os.path.join(run, "plots")
    os.makedirs(out, exist_ok=True)
    dot_plot(summ, app, os.path.join(out, f"{app}_primary.png"))
    if app == "memcached":
        load_curve(summ, os.path.join(out, "memcached_load_curve.png"))
    print(f"\nplots: {out}")


if __name__ == "__main__":
    main()
