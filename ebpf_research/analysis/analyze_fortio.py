#!/usr/bin/env python3
"""
Detailed Fortio JSON analysis and visualization.
Extracts latency histograms and connection metrics from raw Fortio output.
"""

import json
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional
import sys

sns.set_style("whitegrid")
plt.rcParams['figure.figsize'] = (14, 8)


class FortioAnalyzer:
    """Analyze Fortio JSON output files."""
    
    CONFIGS = {
        'baseline': '#2ecc71',
        'sidecar_isolation': '#3498db',
        'sidecarless_contention': '#e74c3c'
    }
    
    def __init__(self, raw_dir: Path):
        self.raw_dir = raw_dir
        self.files = sorted(raw_dir.glob('*_fortio.json'))
        print(f"Found {len(self.files)} Fortio JSON files")
    
    def load_fortio_json(self, filepath: Path) -> Optional[Dict]:
        """Load and parse Fortio JSON output."""
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            print(f"Warning: Could not load {filepath.name}: {e}")
            return None
    
    def extract_histogram(self, data: Dict) -> Optional[pd.DataFrame]:
        """Extract histogram data from Fortio JSON."""
        if not data or 'DurationHistogram' not in data:
            return None
        
        hist = data['DurationHistogram']
        if 'Percentiles' not in hist:
            return None
        
        percentiles = hist['Percentiles']
        hist_df = pd.DataFrame([
            {
                'Percentile': p['Percentile'],
                'Value (ms)': p['Value'] * 1000  # Convert to milliseconds
            }
            for p in percentiles
        ])
        return hist_df
    
    def extract_metrics(self, data: Dict) -> Dict:
        """Extract summary metrics from Fortio JSON."""
        if not data:
            return {}
        
        return {
            'ActualQPS': data.get('ActualQPS', np.nan),
            'TotalRequests': data.get('Count', np.nan),
            'Successes': data.get('Successes', np.nan),
            'Failures': data.get('Errors', np.nan),
            'RequestedQPS': data.get('RequestedQPS', np.nan),
            'DurationSeconds': data.get('DurationSeconds', 0),
        }
    
    def plot_latency_distribution(self, output_dir: Path):
        """Plot latency histograms from all Fortio runs."""
        fig, axes = plt.subplots(1, len(self.files), figsize=(5*len(self.files), 5))
        if len(self.files) == 1:
            axes = [axes]
        
        fig.suptitle('Latency Distribution from Fortio Measurements', 
                    fontsize=14, fontweight='bold', y=1.02)
        
        for ax, filepath in zip(axes, self.files):
            config_name = filepath.stem.replace('_fortio', '')
            data = self.load_fortio_json(filepath)
            
            if data and 'DurationHistogram' in data:
                hist = data['DurationHistogram']
                if 'Count' in hist and isinstance(hist['Count'], list) and len(hist['Count']) > 0:
                    latencies = hist['Count']
                    bucket_labels = range(len(latencies))
                    total = sum(latencies) if isinstance(latencies, list) else latencies
                    
                    color = self.CONFIGS.get(config_name, '#95a5a6')
                    ax.bar(bucket_labels, latencies, color=color, alpha=0.7)
                    ax.set_xlabel('Latency Bucket')
                    ax.set_ylabel('Request Count')
                    ax.set_title(f'{config_name}\n(n={total})')
                    ax.grid(True, alpha=0.3, axis='y')
                else:
                    ax.text(0.5, 0.5, 'No histogram data', 
                           ha='center', va='center', transform=ax.transAxes)
                    ax.set_title(config_name)
        
        plt.tight_layout()
        plt.savefig(output_dir / 'fortio_latency_distribution.png', dpi=300, bbox_inches='tight')
        print(f"✓ Saved: fortio_latency_distribution.png")
        plt.close()
    
    def plot_percentile_curves(self, output_dir: Path):
        """Plot percentile curves across all configs."""
        fig, ax = plt.subplots(figsize=(12, 7))
        
        for filepath in self.files:
            config_name = filepath.stem.replace('_fortio', '')
            data = self.load_fortio_json(filepath)
            
            if data and 'DurationHistogram' in data:
                hist_data = self.extract_histogram(data)
                if hist_data is not None and not hist_data.empty:
                    color = self.CONFIGS.get(config_name, '#95a5a6')
                    ax.plot(hist_data['Percentile'], hist_data['Value (ms)'], 
                           marker='o', label=config_name, linewidth=2.5, 
                           color=color, markersize=6)
        
        ax.set_xlabel('Percentile')
        ax.set_ylabel('Latency (ms)')
        ax.set_title('Latency Percentile Curves from Fortio Runs')
        ax.set_xscale('log')
        ax.set_yscale('log')
        ax.legend(loc='best', fontsize=11)
        ax.grid(True, alpha=0.3, which='both')
        
        plt.tight_layout()
        plt.savefig(output_dir / 'fortio_percentile_curves.png', dpi=300, bbox_inches='tight')
        print(f"✓ Saved: fortio_percentile_curves.png")
        plt.close()
    
    def generate_fortio_summary(self, output_path: Path):
        """Generate summary table from Fortio metrics."""
        summaries = []

        def to_int_or_na(value):
            if value is None or pd.isna(value):
                return 'N/A'
            return int(value)
        
        for filepath in self.files:
            config_name = filepath.stem.replace('_fortio', '')
            data = self.load_fortio_json(filepath)
            
            if data:
                metrics = self.extract_metrics(data)
                hist_data = self.extract_histogram(data)
                
                summary = {
                    'Config': config_name,
                    'ActualQPS': f"{metrics.get('ActualQPS', np.nan):.1f}",
                    'TotalRequests': to_int_or_na(metrics.get('TotalRequests', np.nan)),
                    'Successes': to_int_or_na(metrics.get('Successes', np.nan)),
                    'Failures': to_int_or_na(metrics.get('Failures', np.nan)),
                }
                
                if hist_data is not None and not hist_data.empty:
                    p50 = hist_data[hist_data['Percentile'] >= 50].iloc[0]['Value (ms)'] if len(hist_data) > 0 else np.nan
                    p95 = hist_data[hist_data['Percentile'] >= 95].iloc[0]['Value (ms)'] if len(hist_data[hist_data['Percentile'] >= 95]) > 0 else np.nan
                    p99 = hist_data[hist_data['Percentile'] >= 99].iloc[0]['Value (ms)'] if len(hist_data[hist_data['Percentile'] >= 99]) > 0 else np.nan
                    
                    summary['P50 (ms)'] = f"{p50:.3f}" if not pd.isna(p50) else 'N/A'
                    summary['P95 (ms)'] = f"{p95:.3f}" if not pd.isna(p95) else 'N/A'
                    summary['P99 (ms)'] = f"{p99:.3f}" if not pd.isna(p99) else 'N/A'
                
                summaries.append(summary)
        
        summary_df = pd.DataFrame(summaries)
        summary_df.to_csv(output_path, index=False)
        print(f"✓ Saved: fortio_summary.csv")
        return summary_df


def main():
    """Main analysis pipeline."""
    script_dir = Path(__file__).parent
    raw_dir = script_dir.parent / 'results' / 'raw'
    output_dir = script_dir.parent / 'results' / 'graphs'
    
    if not raw_dir.exists():
        print(f"Error: raw results directory not found at {raw_dir}")
        sys.exit(1)
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Analyzing Fortio JSON files from {raw_dir}...")
    analyzer = FortioAnalyzer(raw_dir)
    
    if len(analyzer.files) == 0:
        print("No Fortio JSON files found.")
        return
    
    print("\nGenerating Fortio visualizations...")
    analyzer.plot_latency_distribution(output_dir)
    analyzer.plot_percentile_curves(output_dir)
    
    print("\nGenerating Fortio summary statistics...")
    summary_df = analyzer.generate_fortio_summary(output_dir / 'fortio_summary.csv')
    print(summary_df.to_string(index=False))
    
    print(f"\n✓ Fortio analysis complete. Graphs saved to: {output_dir}")
    print("   - fortio_latency_distribution.png")
    print("   - fortio_percentile_curves.png")
    print("   - fortio_summary.csv")


if __name__ == '__main__':
    main()
