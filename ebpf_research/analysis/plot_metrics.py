#!/usr/bin/env python3
"""
Comprehensive visualization script for eBPF noisy-neighbor benchmark results.
Generates latency, throughput, and contention analysis graphs.
"""

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import seaborn as sns
import numpy as np
from pathlib import Path
import sys

# Style configuration
sns.set_style("whitegrid")
plt.rcParams['figure.figsize'] = (14, 10)
plt.rcParams['font.size'] = 10
plt.rcParams['axes.labelsize'] = 11
plt.rcParams['axes.titlesize'] = 13
plt.rcParams['xtick.labelsize'] = 10
plt.rcParams['ytick.labelsize'] = 10
plt.rcParams['legend.fontsize'] = 10

COLORS = {
    'baseline': '#2ecc71',
    'sidecar_isolation': '#3498db',
    'sidecarless_contention': '#e74c3c'
}

CONFIG_LABELS = {
    'baseline': 'Baseline\n(No Noise)',
    'sidecar_isolation': 'Sidecar Isolation\n(Separate eBPF)',
    'sidecarless_contention': 'Sidecarless\n(Shared eBPF)'
}


def load_metrics(csv_path):
    """Load and preprocess metrics CSV."""
    df = pd.read_csv(csv_path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # Convert NA strings to NaN for numeric columns
    numeric_cols = ['p50_ms', 'p95_ms', 'p99_ms', 'throughput_qps', 'attacker_rps']
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    return df.sort_values('timestamp')


def plot_latency_comparison(df, output_dir):
    """Generate latency percentile comparison graph."""
    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    fig.suptitle('Latency Percentile Comparison Across Configurations', 
                 fontsize=14, fontweight='bold', y=1.02)
    
    percentiles = ['p50_ms', 'p95_ms', 'p99_ms']
    percentile_labels = ['P50 (Median)', 'P95', 'P99 (Tail)']
    
    for idx, (col, label) in enumerate(zip(percentiles, percentile_labels)):
        ax = axes[idx]
        
        # Group by config and compute stats
        for config in ['baseline', 'sidecar_isolation', 'sidecarless_contention']:
            data = df[df['config'] == config][col].dropna()
            if len(data) > 0:
                x_pos = list(range(len(data)))
                ax.scatter(x_pos, data, color=COLORS[config], 
                          label=CONFIG_LABELS[config], s=100, alpha=0.7)
                # Plot trend line
                if len(data) > 1:
                    z = np.polyfit(x_pos, data, 1)
                    p = np.poly1d(z)
                    ax.plot(x_pos, p(x_pos), color=COLORS[config], 
                           linestyle='--', alpha=0.5, linewidth=2)
        
        ax.set_xlabel('Run Number')
        ax.set_ylabel(f'{label} (ms)')
        ax.set_title(label)
        ax.grid(True, alpha=0.3)
        ax.legend(loc='best')
        ax.set_yscale('log')
    
    plt.tight_layout()
    plt.savefig(output_dir / 'latency_comparison.png', dpi=300, bbox_inches='tight')
    print(f"✓ Saved: latency_comparison.png")
    plt.close()


def plot_throughput_impact(df, output_dir):
    """Generate throughput degradation analysis."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle('Throughput Impact Analysis', fontsize=14, fontweight='bold')

    configs = ['baseline', 'sidecar_isolation', 'sidecarless_contention']

    # Plot 1: Throughput by configuration
    throughput_df = df[['config', 'throughput_qps']].dropna().copy()
    counts = throughput_df.groupby('config').size().reindex(configs, fill_value=0)

    if (counts >= 2).any():
        sns.boxplot(
            data=throughput_df,
            x='config',
            y='throughput_qps',
            order=configs,
            palette=[COLORS[c] for c in configs],
            ax=ax1,
            width=0.55,
            fliersize=3
        )
        sns.stripplot(
            data=throughput_df,
            x='config',
            y='throughput_qps',
            order=configs,
            ax=ax1,
            color='#2c3e50',
            size=5,
            jitter=0.12,
            alpha=0.8
        )
        ax1.set_title('Victim Throughput Distribution')
    else:
        means = [
            throughput_df[throughput_df['config'] == c]['throughput_qps'].mean()
            if counts[c] > 0 else np.nan
            for c in configs
        ]
        x = np.arange(len(configs))
        ax1.bar(x, means, color=[COLORS[c] for c in configs], alpha=0.75, width=0.55)
        ax1.scatter(x, means, color='black', s=35, zorder=3)
        for i, config in enumerate(configs):
            if not np.isnan(means[i]):
                ax1.text(i, means[i], f" n={int(counts[config])}", va='bottom', ha='center', fontsize=9)
        ax1.set_xticks(x)
        ax1.set_title('Victim Throughput (Single-Sample Snapshot)')

    ax1.set_xticklabels([CONFIG_LABELS[c] for c in configs])
    ax1.set_ylabel('Throughput (requests/sec)')
    ax1.grid(True, alpha=0.3, axis='y')

    # Plot 2: Attacker RPS over chronological runs
    run_df = df.sort_values('timestamp').reset_index(drop=True).copy()
    run_df['run_index'] = run_df.index

    for config in configs:
        config_data = run_df[run_df['config'] == config][['run_index', 'attacker_rps']].dropna()
        if len(config_data) > 0:
            ax2.scatter(
                config_data['run_index'],
                config_data['attacker_rps'],
                color=COLORS[config],
                label=CONFIG_LABELS[config],
                s=110,
                alpha=0.8
            )
            if len(config_data) > 1:
                ax2.plot(
                    config_data['run_index'],
                    config_data['attacker_rps'],
                    color=COLORS[config],
                    linewidth=1.5,
                    alpha=0.5
                )

    ax2.set_xlabel('Run Index (chronological)')
    ax2.set_ylabel('Attacker RPS')
    ax2.set_title('Attacker Load Generation')
    ax2.legend(loc='best')
    ax2.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(output_dir / 'throughput_impact.png', dpi=300, bbox_inches='tight')
    print(f"✓ Saved: throughput_impact.png")
    plt.close()


def plot_contention_effect(df, output_dir):
    """Visualize noisy neighbor effect: latency degradation with increasing load."""
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle('Noisy Neighbor Contention Effect', fontsize=14, fontweight='bold')
    
    # Plot 1: P99 latency vs attacker RPS
    ax = axes[0, 0]
    for config in ['baseline', 'sidecar_isolation', 'sidecarless_contention']:
        data = df[df['config'] == config][['p99_ms', 'attacker_rps']].dropna()
        if len(data) > 0:
            ax.scatter(data['attacker_rps'], data['p99_ms'], 
                      color=COLORS[config], label=CONFIG_LABELS[config],
                      s=100, alpha=0.7)
    ax.set_xlabel('Attacker RPS')
    ax.set_ylabel('P99 Latency (ms)')
    ax.set_title('P99 Latency vs Attacker Load')
    ax.set_yscale('log')
    ax.legend(loc='best')
    ax.grid(True, alpha=0.3)
    
    # Plot 2: P50 vs P99 ratio (tail amplification)
    ax = axes[0, 1]
    for config in ['baseline', 'sidecar_isolation', 'sidecarless_contention']:
        data = df[df['config'] == config][['p50_ms', 'p99_ms', 'attacker_rps']].dropna()
        if len(data) > 0:
            ratio = data['p99_ms'] / (data['p50_ms'] + 0.001)  # Avoid division by zero
            ax.scatter(data['attacker_rps'], ratio, 
                      color=COLORS[config], label=CONFIG_LABELS[config],
                      s=100, alpha=0.7)
    ax.set_xlabel('Attacker RPS')
    ax.set_ylabel('P99/P50 Ratio (Tail Amplification)')
    ax.set_title('Tail Latency Amplification')
    ax.set_yscale('log')
    ax.legend(loc='best')
    ax.grid(True, alpha=0.3)
    
    # Plot 3: Config comparison - average metrics
    ax = axes[1, 0]
    configs = ['baseline', 'sidecar_isolation', 'sidecarless_contention']
    x = np.arange(len(configs))
    width = 0.35
    
    p99_means = []
    p99_stds = []
    for config in configs:
        data = df[df['config'] == config]['p99_ms'].dropna()
        if len(data) > 0:
            p99_means.append(data.mean())
            std_val = data.std()
            p99_stds.append(0 if pd.isna(std_val) else std_val)
        else:
            p99_means.append(0)
            p99_stds.append(0)
    
    bars = ax.bar(x, p99_means, width, yerr=p99_stds, 
                  color=[COLORS[c] for c in configs], alpha=0.7, capsize=5)
    ax.set_ylabel('P99 Latency (ms)')
    ax.set_title('Average P99 Latency by Configuration')
    ax.set_xticks(x)
    ax.set_xticklabels([CONFIG_LABELS[c] for c in configs])
    ax.grid(True, alpha=0.3, axis='y')
    
    # Plot 4: Time series view - all metrics
    ax = axes[1, 1]
    for config in configs:
        data = df[df['config'] == config].sort_values('timestamp')
        ax.plot(data.index, data['p99_ms'], marker='o', 
               color=COLORS[config], label=CONFIG_LABELS[config],
               linewidth=2, markersize=6, alpha=0.7)
    ax.set_xlabel('Run Index (chronological)')
    ax.set_ylabel('P99 Latency (ms)')
    ax.set_title('P99 Latency Over Time')
    ax.legend(loc='best')
    ax.grid(True, alpha=0.3)
    ax.set_yscale('log')
    
    plt.tight_layout()
    plt.savefig(output_dir / 'contention_effect.png', dpi=300, bbox_inches='tight')
    print(f"✓ Saved: contention_effect.png")
    plt.close()


def plot_summary_heatmap(df, output_dir):
    """Generate summary heatmap of metrics by configuration."""
    fig, ax = plt.subplots(figsize=(10, 6))
    
    configs = ['baseline', 'sidecar_isolation', 'sidecarless_contention']
    metrics = ['p50_ms', 'p99_ms', 'throughput_qps', 'attacker_rps']
    metric_labels = ['P50 (ms)', 'P99 (ms)', 'Throughput (QPS)', 'Attacker (RPS)']
    
    # Normalize metrics for heatmap (0-1 scale per metric)
    heatmap_data = []
    for config in configs:
        row = []
        for metric in metrics:
            data = df[df['config'] == config][metric].dropna()
            if len(data) > 0:
                mean = data.mean()
                row.append(mean)
            else:
                row.append(np.nan)
        heatmap_data.append(row)
    
    heatmap_array = np.array(heatmap_data)
    
    # Normalize each column independently
    normalized = np.zeros_like(heatmap_array, dtype=float)
    for i in range(heatmap_array.shape[1]):
        col = heatmap_array[:, i]
        col_min = np.nanmin(col)
        col_max = np.nanmax(col)
        if col_max > col_min:
            normalized[:, i] = (col - col_min) / (col_max - col_min)
    
    # Create heatmap
    sns.heatmap(normalized, annot=heatmap_array, fmt='.1f', 
               cmap='RdYlGn_r', cbar_kws={'label': 'Normalized Score'},
               xticklabels=metric_labels,
               yticklabels=[CONFIG_LABELS[c] for c in configs],
               ax=ax, vmin=0, vmax=1)
    
    ax.set_title('Summary Metrics Heatmap\n(Raw values shown, colors normalized per metric)', 
                fontsize=12, fontweight='bold')
    plt.tight_layout()
    plt.savefig(output_dir / 'summary_heatmap.png', dpi=300, bbox_inches='tight')
    print(f"✓ Saved: summary_heatmap.png")
    plt.close()


def generate_summary_table(df, output_path):
    """Generate a summary statistics table."""
    configs = ['baseline', 'sidecar_isolation', 'sidecarless_contention']
    
    summary_data = []
    for config in configs:
        data = df[df['config'] == config]
        summary_data.append({
            'Configuration': CONFIG_LABELS[config],
            'P50 Mean (ms)': f"{data['p50_ms'].mean():.3f}",
            'P99 Mean (ms)': f"{data['p99_ms'].mean():.3f}",
            'Throughput Mean (QPS)': f"{data['throughput_qps'].mean():.1f}",
            'Attacker RPS Mean': f"{data['attacker_rps'].mean():.1f}",
            'P50 Std (ms)': f"{data['p50_ms'].std():.3f}",
            'P99 Std (ms)': f"{data['p99_ms'].std():.3f}",
            'Sample Count': len(data)
        })
    
    summary_df = pd.DataFrame(summary_data)
    summary_df.to_csv(output_path, index=False)
    print(f"✓ Saved: summary_statistics.csv")
    return summary_df


def main():
    """Main visualization pipeline."""
    script_dir = Path(__file__).parent
    metrics_path = script_dir.parent / 'results' / 'metrics.csv'
    output_dir = script_dir.parent / 'results' / 'graphs'
    
    if not metrics_path.exists():
        print(f"Error: metrics.csv not found at {metrics_path}")
        sys.exit(1)
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Loading metrics from {metrics_path}...")
    df = load_metrics(metrics_path)
    
    print(f"Loaded {len(df)} benchmark records")
    print(f"Configurations: {df['config'].unique().tolist()}")
    print()
    
    print("Generating visualizations...")
    plot_latency_comparison(df, output_dir)
    plot_throughput_impact(df, output_dir)
    plot_contention_effect(df, output_dir)
    plot_summary_heatmap(df, output_dir)
    
    print("\nGenerating summary statistics...")
    summary_df = generate_summary_table(df, output_dir / 'summary_statistics.csv')
    print(summary_df.to_string(index=False))
    
    print(f"\n✓ All visualizations saved to: {output_dir}")
    print("   - latency_comparison.png")
    print("   - throughput_impact.png")
    print("   - contention_effect.png")
    print("   - summary_heatmap.png")
    print("   - summary_statistics.csv")


if __name__ == '__main__':
    main()
