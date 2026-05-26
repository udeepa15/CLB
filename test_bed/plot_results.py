#!/usr/bin/env python3
import json
import glob
import os
import sys
import matplotlib.pyplot as plt
import numpy as np

# Set premium styling settings
plt.style.use('seaborn-v0_8-whitegrid' if 'seaborn-v0_8-whitegrid' in plt.style.available else 'default')
plt.rcParams.update({
    'font.size': 11,
    'axes.labelsize': 12,
    'axes.titlesize': 13,
    'xtick.labelsize': 10,
    'ytick.labelsize': 10,
    'figure.titlesize': 16,
    'font.family': 'sans-serif'
})

def get_latest_dir(arch):
    dirs = sorted(glob.glob(f"results/{arch}/*"))
    return dirs[-1] if dirs else None

def parse_fortio_file(filepath):
    with open(filepath, 'r') as f:
        data = json.load(f)
    
    percentiles = data['DurationHistogram']['Percentiles']
    p50, p90, p99, p999 = None, None, None, None
    for p in percentiles:
        # Convert seconds to milliseconds
        val_ms = p['Value'] * 1000.0
        if p['Percentile'] == 50:
            p50 = val_ms
        elif p['Percentile'] == 90:
            p90 = val_ms
        elif p['Percentile'] == 99:
            p99 = val_ms
        elif p['Percentile'] == 99.9:
            p999 = val_ms
    return p50, p90, p99, p999

def main():
    sidecar_dir = get_latest_dir("sidecar")
    sidecarless_dir = get_latest_dir("sidecarless")

    if not sidecar_dir or not sidecarless_dir:
        print("ERROR: Sidecar or Sidecarless results directory is missing.", file=sys.stderr)
        sys.exit(1)

    print(f"Plotting results from:")
    print(f"  Sidecar:     {sidecar_dir}")
    print(f"  Sidecarless: {sidecarless_dir}")

    rps_values = [0, 10000, 20000, 30000]
    
    sc_data = { 'p50': [], 'p90': [], 'p99': [], 'p999': [] }
    sl_data = { 'p50': [], 'p90': [], 'p99': [], 'p999': [] }

    for rps in rps_values:
        # Sidecar
        sc_file = os.path.join(sidecar_dir, f"fortio_rps_{rps}.json")
        if os.path.exists(sc_file):
            p50, p90, p99, p999 = parse_fortio_file(sc_file)
            sc_data['p50'].append(p50)
            sc_data['p90'].append(p90)
            sc_data['p99'].append(p99)
            sc_data['p999'].append(p999)
        else:
            print(f"Warning: {sc_file} not found.", file=sys.stderr)
            sys.exit(1)

        # Sidecarless
        sl_file = os.path.join(sidecarless_dir, f"fortio_rps_{rps}.json")
        if os.path.exists(sl_file):
            p50, p90, p99, p999 = parse_fortio_file(sl_file)
            sl_data['p50'].append(p50)
            sl_data['p90'].append(p90)
            sl_data['p99'].append(p99)
            sl_data['p999'].append(p999)
        else:
            print(f"Warning: {sl_file} not found.", file=sys.stderr)
            sys.exit(1)

    # 1x3 subplots for P50, P90, and P99 latency
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.5))
    metrics = [('p50', 'Median (P50) Latency', axes[0]),
               ('p90', 'P90 Latency', axes[1]),
               ('p99', 'Tail (P99) Latency', axes[2])]

    # Cohesive premium color palette (Classic Indigo vs Vivid Orange/Red)
    sc_color = '#1f77b4'
    sl_color = '#ff7f0e'

    for metric_name, title, ax in metrics:
        ax.plot(rps_values, sc_data[metric_name], marker='s', linestyle='--', linewidth=2.5, markersize=8, color=sc_color, label='Sidecar Proxy (Baseline)')
        ax.plot(rps_values, sl_data[metric_name], marker='o', linestyle='-', linewidth=2.5, markersize=8, color=sl_color, label='Sidecarless (eBPF Redirect)')
        
        ax.set_title(title, fontsize=13, fontweight='bold', pad=10)
        ax.set_xlabel('Attacker Load (RPS)', fontsize=11, labelpad=8)
        ax.set_ylabel('Latency (ms)', fontsize=11, labelpad=8)
        ax.set_xticks(rps_values)
        ax.grid(True, linestyle=':', alpha=0.6)
        
        # Format labels nicely
        ax.tick_params(axis='both', which='major', labelsize=10)

    # Set single legend on the top/middle
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5, 0.95), ncol=2, frameon=True, fontsize=12)

    plt.suptitle('Latency Analysis: Sidecar vs Sidecarless Topology', fontsize=16, fontweight='bold', y=1.02)
    plt.tight_layout(rect=[0, 0, 1, 0.88])
    
    # Save the output file
    output_png = 'results/latency_comparison.png'
    plt.savefig(output_png, dpi=200, bbox_inches='tight')
    print(f"Plot successfully saved to: {os.path.abspath(output_png)}")

if __name__ == '__main__':
    main()
