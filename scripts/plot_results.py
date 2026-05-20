#!/usr/bin/env python3
import pandas as pd
from pathlib import Path
import matplotlib.pyplot as plt
import seaborn as sns

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent / 'ebpf_research'
CSV = ROOT / 'sidecar_vs_sidecarless_metrics.csv'
OUTDIR = ROOT / 'results' / 'plots'
OUTDIR.mkdir(parents=True, exist_ok=True)

def load():
    df = pd.read_csv(CSV)
    df['attacker_rate'] = pd.to_numeric(df['attacker_rate'], errors='coerce')
    df['target_qps'] = pd.to_numeric(df['target_qps'], errors='coerce')
    df['actual_qps'] = pd.to_numeric(df['actual_qps'], errors='coerce')
    for col in ['p50_us','p95_us','p99_us','p999_us']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df

def plot_throughput(df):
    # aggregate victims by mode and rate
    agg = df[df['victim']!='adv'].groupby(['mode','attacker_rate'])['actual_qps'].sum().reset_index()
    plt.figure(figsize=(8,5))
    sns.lineplot(data=agg, x='attacker_rate', y='actual_qps', hue='mode', marker='o')
    plt.xlabel('Attacker Rate (RPS)')
    plt.ylabel('Combined Victim Throughput (RPS)')
    plt.title('Throughput vs Attacker Rate')
    plt.grid(True)
    plt.savefig(OUTDIR / 'throughput_vs_attacker_rate.png', bbox_inches='tight')

def plot_percentiles(df):
    pct_cols = ['p50_us','p95_us','p99_us','p999_us']
    for col in pct_cols:
        agg = df[df['victim']!='adv'].groupby(['mode','attacker_rate'])[col].mean().reset_index()
        plt.figure(figsize=(8,5))
        sns.lineplot(data=agg, x='attacker_rate', y=col, hue='mode', marker='o')
        plt.xlabel('Attacker Rate (RPS)')
        plt.ylabel(f'{col} (us)')
        plt.title(f'{col} vs Attacker Rate')
        plt.grid(True)
        plt.savefig(OUTDIR / f'{col}_vs_attacker_rate.png', bbox_inches='tight')

def main():
    if not CSV.exists():
        print('CSV not found, run aggregate_results.py first')
        return
    df = load()
    plot_throughput(df)
    plot_percentiles(df)
    print('Plots written to', OUTDIR)

if __name__ == '__main__':
    main()
