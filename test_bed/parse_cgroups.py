import json
import glob
import os

for vic in ["vic1", "vic2", "vic3"]:
    print(f"=== {vic.upper()} ===")
    print(f"{'Limit':<8} {'P50 (ms)':<10} {'P90 (ms)':<10} {'P99 (ms)':<10} {'P99.9 (ms)':<10} {'Max (ms)':<10}")
    print("-" * 58)
    for limit in [80, 85, 90, 95, 99]:
        p = f"/home/udeepa/CLB/test_bed/results/cgroups/20260721_213813/fortio_{vic}_limit_{limit}.json"
        if not os.path.exists(p):
            continue
        with open(p) as f:
            d = json.load(f)
        pcts = {x["Percentile"]: x["Value"] * 1000.0 for x in d["DurationHistogram"]["Percentiles"]}
        p50 = pcts.get(50, 0.0)
        p90 = pcts.get(90, 0.0)
        p99 = pcts.get(99, 0.0)
        p999 = pcts.get(99.9, 0.0)
        mx = d["DurationHistogram"]["Max"] * 1000.0
        print(f"{limit}%{'':<5} {p50:<10.2f} {p90:<10.2f} {p99:<10.2f} {p999:<10.2f} {mx:<10.2f}")
    print()
