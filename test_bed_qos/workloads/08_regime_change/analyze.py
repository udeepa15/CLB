#!/usr/bin/env python3
"""analyze.py - Workload 08 (regime change): stats + plot. Primary metric: phaseB_p99_ms
(victim P99 after the attacker changes behaviour); phaseA_p99_ms is the calm control."""
import glob, os, sys
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns

sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shared"))
import stats_common


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dirs = sorted(glob.glob(os.path.join(script_dir, "results", "multi_run_*")), key=os.path.getmtime)
    if not dirs:
        print("No results directory found in 08_regime_change."); return
    latest = dirs[-1]
    df = pd.read_csv(os.path.join(latest, "results_summary_raw.csv"))
    print("=== Workload 08: Regime Change Analysis ===")
    print(df.groupby("arch")[["phaseA_p99_ms", "phaseB_p99_ms"]].median())
    stats_common.report(df, metrics=["phaseA_p99_ms", "phaseB_p99_ms"], group_cols=[],
                        out_csv=os.path.join(latest, "stats"))
    melted = df.melt(id_vars=["arch", "rep"], value_vars=["phaseA_p99_ms", "phaseB_p99_ms"],
                     var_name="phase", value_name="p99_ms")
    plt.figure(figsize=(10, 5))
    sns.boxplot(data=melted, x="phase", y="p99_ms", hue="arch")
    plt.title("Workload 08: victim P99 before (A) and after (B) attacker regime change")
    plt.ylabel("Victim P99 latency (ms)")
    out = os.path.join(latest, "plots"); os.makedirs(out, exist_ok=True)
    plt.savefig(os.path.join(out, "regime_change_p99.png"), dpi=300, bbox_inches="tight")
    print("plot saved to", out)


if __name__ == "__main__":
    main()
