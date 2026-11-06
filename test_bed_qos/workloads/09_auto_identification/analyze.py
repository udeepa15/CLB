#!/usr/bin/env python3
"""analyze.py - Workload 09: victim latency per scenario + identification quality of the auto arms.

Outputs (in the latest results dir): stats_summary.csv / stats_pairwise.csv (P99, per scenario),
classification.csv (precision, recall, time-to-classify, false-positive ticks per trial) and a plot.
NaN means "not applicable" (e.g. recall when no attacker exists), never zero.
"""
import glob
import json
import os
import sys

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns

sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shared"))
import stats_common
from classify_metrics import classification_metrics


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dirs = sorted(glob.glob(os.path.join(script_dir, "results", "multi_run_*")), key=os.path.getmtime)
    if not dirs:
        print("No results directory found in 09_auto_identification."); return
    latest = dirs[-1]
    df = pd.read_csv(os.path.join(latest, "results_summary_raw.csv"))
    print("=== Workload 09: Automatic Identification ===")

    # A trial whose load generator never actually delivered packets measures "no attack", not the
    # scenario. The runner verifies each flood and records flood_ok; drop the trials that failed.
    def _flood_ok(r):
        try:
            return json.load(open(os.path.join(latest, "raw", f"truth_{r['arch']}_{r['scenario']}_rep_{r['rep']}.json"))).get("flood_ok", True)
        except FileNotFoundError:
            return False
    ok = df.apply(_flood_ok, axis=1)
    if (~ok).any():
        print(f"EXCLUDED {int((~ok).sum())}/{len(df)} trials with an unverified load generator:")
        print(df[~ok][["arch", "scenario", "rep"]].to_string(index=False))
    df = df[ok].reset_index(drop=True)
    print(df.groupby(["scenario", "arch"])["p99_ms"].median().unstack())
    stats_common.report(df, metrics=["p99_ms"], group_cols=["scenario"], out_csv=os.path.join(latest, "stats"))

    rows = []
    for _, r in df[df["arch"].str.startswith("qos_auto")].iterrows():
        tid = f"{r['arch']}_{r['scenario']}_rep_{r['rep']}"
        raw = os.path.join(latest, "raw")
        try:
            truth = json.load(open(os.path.join(raw, f"truth_{tid}.json")))
        except FileNotFoundError:
            continue
        mode = "port" if r["arch"].endswith("port") else "ip"
        labels = truth["attacker_port_labels"] if mode == "port" else truth["attacker_ip_labels"]
        m = classification_metrics(os.path.join(raw, f"qos_controller_{tid}.jsonl"), labels, truth["start_epoch"])
        rows.append({"arch": r["arch"], "scenario": r["scenario"], "rep": r["rep"],
                     "p99_ms": r["p99_ms"], "spoofed": truth["spoofed_sources"], **m})
    if rows:
        cl = pd.DataFrame(rows)
        cl.to_csv(os.path.join(latest, "classification.csv"), index=False)
        pd.set_option("display.width", 200)
        print("\n--- Identification quality (median over reps; NaN = not applicable) ---")
        print(cl.groupby(["scenario", "arch"])[["precision", "recall", "time_to_classify_s", "fp_ticks", "max_sources"]]
              .median().round(3).to_string())

    plt.figure(figsize=(11, 5))
    sns.boxplot(data=df, x="scenario", y="p99_ms", hue="arch")
    plt.ylabel("Victim P99 latency (ms)"); plt.ylim(bottom=0); plt.grid(axis="y", alpha=0.3)
    plt.legend(title="arm", loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False)
    plt.title("Workload 09: victim P99 per scenario and arm")
    out = os.path.join(latest, "plots"); os.makedirs(out, exist_ok=True)
    plt.savefig(os.path.join(out, "auto_identification_p99.png"), dpi=300, bbox_inches="tight")


if __name__ == "__main__":
    main()
