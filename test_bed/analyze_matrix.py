#!/usr/bin/env python3
import os
import sys
import csv
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats

def parse_ebpf_hist(hist_file):
    # Simplistic parser for bpftool map dump output
    # Returns median and p99 lock wait time in ns
    if not os.path.exists(hist_file): return None, None
    
    # We will just read the last dumped JSON snapshot (which represents the accumulated state at the end of the run)
    with open(hist_file, 'r') as f:
        lines = f.readlines()
        if not lines: return None, None
        
    try:
        last_dump = json.loads(lines[-1])
        # Each dump is {"timestamp": ..., "histogram": [{"key": [...], "values": [...]}, ...]}
        # We need to aggregate the values across all CPUs
        
        # Simplified for now: just grab it as a raw list
        # Key 0-63 is log2 of the lock latency
        # We can reconstruct a rough array of values to compute percentiles
        latencies = []
        for bucket in last_dump.get("histogram", []):
            try:
                # Key format varies, let's assume it's properly parsed or we just read the formatted key
                if 'formatted' in bucket and 'key' in bucket['formatted']:
                    bucket_idx = int(bucket['formatted']['key'])
                    latency_ns = 2 ** bucket_idx
                    
                    # Sum values across all CPUs
                    count = sum([int(v['value']) for v in bucket['formatted']['values']])
                    if count > 0:
                        latencies.extend([latency_ns] * count)
            except:
                pass
                
        if not latencies: return 0, 0
        latencies = np.array(latencies)
        return np.median(latencies), np.percentile(latencies, 99)
    except:
        return None, None

def parse_fortio(fortio_file):
    if not os.path.exists(fortio_file): return None, None, None
    try:
        with open(fortio_file, 'r') as f:
            data = json.load(f)
            
        p50 = p90 = p99 = None
        for p in data['DurationHistogram']['Percentiles']:
            if p['Percentile'] == 50: p50 = p['Value']
            if p['Percentile'] == 90: p90 = p['Value']
            if p['Percentile'] == 99: p99 = p['Value']
            
        return p50, p90, p99
    except:
        return None, None, None

def main(matrix_dir):
    manifest_path = os.path.join(matrix_dir, "manifest.csv")
    if not os.path.exists(manifest_path):
        print(f"Manifest not found in {matrix_dir}")
        sys.exit(1)
        
    results = []
    with open(manifest_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['status'] != 'SUCCESS': continue
            
            run_id = row['run_id']
            run_dir = os.path.join(matrix_dir, f"run_{run_id}")
            
            f_p50, f_p90, f_p99 = parse_fortio(os.path.join(run_dir, "fortio.json"))
            lock_p50, lock_p99 = parse_ebpf_hist(os.path.join(run_dir, "ebpf.jsonl"))
            
            results.append({
                'isolation': row['isolation'],
                'quota': int(row['quota']),
                'period': int(row['period']),
                'offset': int(row['offset']),
                'fortio_p50': f_p50,
                'fortio_p99': f_p99,
                'lock_p50_ns': lock_p50,
                'lock_p99_ns': lock_p99
            })
            
    df = pd.DataFrame(results)
    if df.empty:
        print("No successful runs found.")
        return
        
    print(f"Loaded {len(df)} successful runs.")
    
    # 1. Base comparison plot
    base_df = df[(df['quota'] == 0) | (df['quota'] == 90)]
    plt.figure(figsize=(12, 6))
    sns.boxplot(data=base_df, x='isolation', y='fortio_p99')
    plt.title("Fortio P99 Latency by Isolation Mode")
    plt.ylabel("Latency (s)")
    plt.savefig(os.path.join(matrix_dir, "isolation_comparison.png"))
    
    # 2. Statistical Analysis: cpuset_irq vs cpu.max(90%)
    cpuset_irq = df[df['isolation'] == 'cpuset_irq']['fortio_p99'].dropna()
    cpu_max = df[(df['isolation'] == 'cpu.max') & (df['quota'] == 90)]['fortio_p99'].dropna()
    
    if len(cpuset_irq) > 0 and len(cpu_max) > 0:
        u_stat, p_val = stats.mannwhitneyu(cpuset_irq, cpu_max, alternative='less')
        print("\n--- Statistical Analysis ---")
        print(f"Mann-Whitney U Test (cpuset_irq < cpu.max @ 90%)")
        print(f"U-statistic: {u_stat}, p-value: {p_val:.5f}")
        if p_val < 0.05:
            print("CONCLUSION: cpuset+IRQ is statistically significantly better than cpu.max.")
        else:
            print("CONCLUSION: Not enough evidence to say cpuset+IRQ is better.")

    print(f"Analysis complete. Plots saved to {matrix_dir}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: ./analyze_matrix.py <results_dir>")
        sys.exit(1)
    main(sys.argv[1])
