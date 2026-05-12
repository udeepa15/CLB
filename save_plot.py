import argparse
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot Phase-2 latency comparison chart")
    parser.add_argument(
        "--input",
        default=None,
        help="Optional path to metrics CSV. If omitted, script uses built-in fallbacks.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Resolve paths relative to this script so it works from any shell cwd.
    repo_root = Path(__file__).resolve().parent

    if args.input:
        csv_path = Path(args.input).expanduser()
        if not csv_path.is_absolute():
            csv_path = (repo_root / csv_path).resolve()
    else:
        # Prefer canonical metrics name; keep metrics1 as legacy fallback.
        candidates = [
            repo_root / "ebpf_research" / "results" / "phase2_5v1_metrics1.csv",
        ]
        csv_path = next((p for p in candidates if p.exists()), candidates[0])

    if not csv_path.exists():
        raise FileNotFoundError(
            f"Metrics CSV not found: {csv_path}. "
            "Expected one of: phase2_5v1_metrics.csv or phase2_5v1_metrics1.csv"
        )
    
    print(f"Loading data from {csv_path}...")
    df = pd.read_csv(csv_path)

    # Group by config and noise_target_rps, then calculate the mean across the 3 run_ids
    avg_df = df.groupby(['config', 'noise_target_rps'])[['p50_ms', 'p95_ms', 'p99_ms']].mean().reset_index()

    # Separate the data into two dataframes for the two configurations
    df_isolated = avg_df[avg_df['config'] == 'sidecar_isolation']
    df_shared = avg_df[avg_df['config'] == 'sidecarless_contention']

    # Apply a clean seaborn theme
    sns.set_theme(style="whitegrid")

    # Create a figure with 2 subplots side-by-side, sharing the Y-axis so they are easy to compare
    fig, axes = plt.subplots(1, 2, figsize=(14, 6), sharey=True)

    # Define standard colors for the percentiles (Green for P50, Orange/Yellow for P95, Red for P99)
    colors = {'p50': '#2ecc71', 'p95': '#f39c12', 'p99': '#e74c3c'}

    # --- Plot 1: Sidecar Isolation ---
    axes[0].plot(df_isolated['noise_target_rps'], df_isolated['p50_ms'], marker='o', label='P50', color=colors['p50'], linewidth=2.5)
    axes[0].plot(df_isolated['noise_target_rps'], df_isolated['p95_ms'], marker='s', label='P95', color=colors['p95'], linewidth=2.5)
    axes[0].plot(df_isolated['noise_target_rps'], df_isolated['p99_ms'], marker='^', label='P99', color=colors['p99'], linewidth=2.5)
    
    axes[0].set_title('Sidecar Model (Strictly Isolated)', fontsize=14, pad=10)
    axes[0].set_xlabel('Attacker Noise (Requests Per Second)', fontsize=12)
    axes[0].set_ylabel('Latency (ms)', fontsize=12)
    axes[0].legend(fontsize=11)

    # --- Plot 2: Sidecarless Contention ---
    axes[1].plot(df_shared['noise_target_rps'], df_shared['p50_ms'], marker='o', label='P50', color=colors['p50'], linewidth=2.5)
    axes[1].plot(df_shared['noise_target_rps'], df_shared['p95_ms'], marker='s', label='P95', color=colors['p95'], linewidth=2.5)
    axes[1].plot(df_shared['noise_target_rps'], df_shared['p99_ms'], marker='^', label='P99', color=colors['p99'], linewidth=2.5)
    
    axes[1].set_title('Sidecarless Model (Shared eBPF Map)', fontsize=14, pad=10)
    axes[1].set_xlabel('Attacker Noise (Requests Per Second)', fontsize=12)
    axes[1].legend(fontsize=11)

    # Add a main title for the entire figure
    fig.suptitle('Latency Degradation under Network eBPF Contention (5v1 Thundering Herd)', fontsize=16, y=1.02)

    # Adjust layout so labels don't overlap
    plt.tight_layout()

    # Save the chart under results/graphs so outputs stay with experiment artifacts.
    output_dir = repo_root / "ebpf_research" / "results" / "graphs"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_img = output_dir / "latency_degradation_chart.png"
    plt.savefig(output_img, dpi=300, bbox_inches='tight')
    print(f"Success! Graph saved as: {output_img}")

if __name__ == "__main__":
    main()