#!/usr/bin/env python3
"""Analyze workload 10 (many tenants). Usage: python3 analyze.py [results_dir]  (default: newest run).

Trials whose floods were not all verified (flood_ok = False) are excluded. Per scenario it reports, for
each metric, per-arm medians with bootstrap CIs and all pairwise Mann-Whitney tests (Bonferroni over the
whole family), and for the automatic arms the identification quality (precision, recall, false-positive
tenants, time to classify). Plots go to <results>/plots/.

Metrics: pooled victim P99 / P99.9 (all clients' histograms merged), the worst client's P99, P99 of
clients whose server is flooded (targeted) and of everyone else (bystanders), errors, CPU.
"""
import glob
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "shared"))
import stats_common  # noqa: E402
from classify_metrics import classification_metrics  # noqa: E402

METRICS = ["p99_ms", "p999_ms", "worst_client_p99_ms", "p99_bystander_ms", "p99_targeted_ms", "errors",
           "cpu_busy_tenants"]


def latest_run():
    runs = [d for d in glob.glob(os.path.join(HERE, "results", "*")) if os.path.isdir(d)]
    if not runs:
        sys.exit("no results")
    return max(runs, key=os.path.getmtime)


def primary_tests(run_dir, ref="qos_open"):
    """Pre-planned primary family: every arm vs `ref` on P99 (and, as a separate secondary family, P99.9)
    in every scenario. stats.csv corrects over ALL metric x pair tests (about 1,000 at 9 arms); at n = 10 per
    arm the smallest attainable Mann-Whitney p (about 1.8e-4) can never pass that, so it is descriptive only."""
    p = pd.read_csv(os.path.join(run_dir, "stats_pairwise.csv"))
    out = []
    for m in ("p99_ms", "p999_ms"):
        q = p[(p.metric == m) & ((p.arm_a == ref) | (p.arm_b == ref))].copy()
        if q.empty:
            continue
        alpha = 0.05 / len(q)
        flip = q.arm_a == ref
        q["arm"] = q.arm_b.where(flip, q.arm_a)
        q["shift_vs_ref_ms"] = (-q.median_diff_a_minus_b).where(flip, q.median_diff_a_minus_b)
        q["cliffs_delta_vs_ref"] = (-q.cliffs_delta).where(flip, q.cliffs_delta)
        q["family"], q["alpha_bonferroni"] = len(q), alpha
        q["significant"] = (q.p < alpha) & (q.n_a >= 3) & (q.n_b >= 3)
        out.append(q[["metric", "scenario", "arm", "shift_vs_ref_ms", "cliffs_delta_vs_ref", "p", "family",
                      "alpha_bonferroni", "significant"]])
        print(f"\n--- primary: {m} shift vs {ref} (Hodges-Lehmann ms; * = significant, family {len(q)}, "
              f"alpha {alpha:.4f}) ---")
        q["cell"] = q.shift_vs_ref_ms.map("{:+.2f}".format) + q.significant.map({True: "*", False: ""})
        print(q.pivot_table(index="arm", columns="scenario", values="cell", aggfunc="first").to_string())
    if out:
        pd.concat(out).to_csv(os.path.join(run_dir, "stats_primary.csv"), index=False)


def identification(run_dir, df):
    rows = []
    for _, r in df[df["arch"].str.startswith("qos_auto")].iterrows():
        tid = f"{r['arch']}_{r['scenario']}_rep_{r['rep']}"
        truth = json.load(open(os.path.join(run_dir, "raw", tid, "truth.json")))
        labels = truth["attacker_port_labels"] if r["arch"].endswith("port") else truth["attacker_ip_labels"]
        m = classification_metrics(os.path.join(run_dir, "raw", tid, "controller.jsonl"), labels, truth["start_epoch"])
        rows.append({"arch": r["arch"], "scenario": r["scenario"], "rep": r["rep"], **m})
    return pd.DataFrame(rows)


STYLE = {"sidecarless": ("#2a78d6", "o"), "qos_open": ("#eb6834", "s"), "qos_static": ("#1baf7a", "^"),
         "qos_dynamic_fixed": ("#eda100", "D"), "qos_dynamic_adaptive": ("#eda100", "o"), "qos_auto_port": ("#e87ba4", "v"),
         "qos_markov": ("#008300", "P"), "qos_markov_200ms": ("#008300", "h"), "qos_markov_l95": ("#008300", "*"),
         "qos_tc_police": ("#6250d6", "X"), "qos_tiered": ("#e34948", "p")}
DOT_ORDER = ["qos_open", "qos_tiered", "qos_static", "qos_tc_police", "qos_dynamic_fixed", "qos_dynamic_adaptive",
             "qos_markov", "qos_markov_200ms", "qos_markov_l95", "qos_auto_port"]
SURFACE, INK, INK2 = "#fcfcfb", "#1f1e1d", "#52514e"


def dot_plot(run_dir, metric, label, exclude=("sidecarless",)):
    """One panel per scenario, one row per arm: median and 95% bootstrap CI, each panel on its own scale.
    '*' after an arm = significantly different from qos_open (primary family). sidecarless is left out because
    it is two orders of magnitude slower and would flatten every other arm; its value goes in the caption."""
    summ = pd.read_csv(os.path.join(run_dir, "stats_summary.csv"))
    summ = summ[(summ.metric == metric) & ~summ.arm.isin(exclude)]
    prim_path = os.path.join(run_dir, "stats_primary.csv")
    prim = pd.read_csv(prim_path) if os.path.exists(prim_path) else pd.DataFrame()
    scen = [s for s in ("none", "a1_flood", "a5_flood", "a5_small") if s in set(summ.scenario)] or sorted(summ.scenario.unique())
    arms_ = [a for a in DOT_ORDER if a in set(summ.arm)]
    ypos = {a: i for i, a in enumerate(reversed(arms_))}
    fig, axes = plt.subplots(1, len(scen), figsize=(3.4 * len(scen), 0.42 * len(arms_) + 1.6), sharey=True,
                             facecolor=SURFACE, squeeze=False)
    for ax, sc in zip(axes[0], scen):
        ax.set_facecolor(SURFACE)
        g = summ[summ.scenario == sc].set_index("arm")
        sig = set()
        if not prim.empty:
            q = prim[(prim.metric == metric) & (prim.scenario == sc) & (prim.significant == True)]  # noqa: E712
            sig = set(q.arm)
        for a in arms_:
            if a not in g.index:
                continue
            c, mk = STYLE.get(a, (INK2, "o"))
            m, lo, hi = g.loc[a, "median"], g.loc[a, "ci95_lo"], g.loc[a, "ci95_hi"]
            if pd.notna(lo) and pd.notna(hi):
                ax.plot([lo, hi], [ypos[a]] * 2, color=c, linewidth=1.6, alpha=0.75)
            ax.plot(m, ypos[a], mk, color=c, markersize=6.5, markeredgecolor=SURFACE, markeredgewidth=0.8)
            if a in sig:
                ax.annotate("*", (hi if pd.notna(hi) else m, ypos[a]), xytext=(4, -3), textcoords="offset points",
                            fontsize=11, color=INK)
        top = float(pd.concat([g["median"], g["ci95_hi"]]).max())
        ax.set_xlim(0, top * 1.15)
        ax.set_title(sc, color=INK, fontsize=9.5, loc="left")
        ax.grid(True, axis="x", color=INK2, alpha=0.18, linewidth=0.6)
        for sp in ("top", "right", "left"):
            ax.spines[sp].set_visible(False)
        ax.tick_params(colors=INK2, labelsize=8)
        ax.set_xlabel(label, color=INK, fontsize=8.5)
    axes[0][0].set_yticks(list(ypos.values()))
    axes[0][0].set_yticklabels([a.replace("qos_", "") for a in ypos], color=INK, fontsize=8.5)
    fig.suptitle(f"100 containers, 10 reps: {label} (median, 95% CI; * = significant vs open)", color=INK,
                 fontsize=10, x=0.01, ha="left")
    fig.tight_layout()
    out = os.path.join(run_dir, "plots")
    os.makedirs(out, exist_ok=True)
    fig.savefig(os.path.join(out, f"{metric}_dots.png"), dpi=200, facecolor=SURFACE)
    plt.close(fig)


def plot(df, run_dir):
    out = os.path.join(run_dir, "plots")
    os.makedirs(out, exist_ok=True)
    scen = sorted(df["scenario"].unique())
    arms = sorted(df["arch"].unique())
    for metric, label in [("p99_ms", "pooled victim P99 (ms)"), ("p999_ms", "pooled victim P99.9 (ms)"),
                          ("worst_client_p99_ms", "worst client P99 (ms)")]:
        fig, axes = plt.subplots(1, len(scen), figsize=(4 * len(scen), 4.2), sharey=True, squeeze=False)
        for ax, s in zip(axes[0], scen):
            data = [df[(df.scenario == s) & (df.arch == a)][metric].dropna() for a in arms]
            ax.boxplot(data, tick_labels=arms, showfliers=True)
            ax.set_title(s)
            ax.tick_params(axis="x", rotation=60)
            ax.grid(axis="y", alpha=0.3)
            ax.set_ylim(bottom=0)
        axes[0][0].set_ylabel(label)
        fig.suptitle(f"Workload 10 ({int(df['n_tenants'].iloc[0])} tenants): {label}")
        fig.tight_layout()
        fig.savefig(os.path.join(out, f"{metric}.png"), dpi=200)
        plt.close(fig)


def main():
    run_dir = sys.argv[1] if len(sys.argv) > 1 else latest_run()
    df = pd.read_csv(os.path.join(run_dir, "results_summary_raw.csv"))
    bad = df[df["flood_ok"] != True]  # noqa: E712
    if len(bad):
        print(f"excluding {len(bad)} trials with unverified floods: {list(bad['arch'] + '/' + bad['scenario'])}")
    df = df[df["flood_ok"] == True].copy()  # noqa: E712
    print(f"{run_dir}\n{len(df)} trials, arms={sorted(df['arch'].unique())}, scenarios={sorted(df['scenario'].unique())}")
    print(f"requests per trial (median) = {int(df['requests'].median())}, total errors = {int(df['errors'].sum())}, "
          f"missing clients = {int(df['missing_clients'].sum())}")
    metrics = [m for m in METRICS if m in df.columns and df[m].notna().any()]
    stats_common.report(df, metrics, group_cols=["scenario"], out_csv=os.path.join(run_dir, "stats.csv"))
    primary_tests(run_dir)
    ctx = df.groupby(["scenario", "arch"])[["ingress_pps", "hits_per_sec", "attack_offered_MBps", "to_targets_MBps",
                                            "cpu_busy_tenants", "cpu_softirq_tenants", "ctrl_tick_ms_p99"]].median()
    print("\n--- context (medians) ---")
    print(ctx.round(3).to_string())
    ident = identification(run_dir, df)
    if not ident.empty:
        ident.to_csv(os.path.join(run_dir, "classification.csv"), index=False)
        print("\n--- identification (median over reps) ---")
        print(ident.groupby(["scenario", "arch"])[["precision", "recall", "fp_tenants", "time_to_classify_s",
                                                   "fp_ticks"]].median().round(3).to_string())
    plot(df, run_dir)
    for m, lab in (("p99_ms", "victim P99 (ms)"), ("p999_ms", "victim P99.9 (ms)")):
        dot_plot(run_dir, m, lab)
    print(f"\nplots: {os.path.join(run_dir, 'plots')}")


if __name__ == "__main__":
    main()
