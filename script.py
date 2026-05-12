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
            repo_root / "ebpf_research" / "results" / "phase2_5v1_metrics.csv"
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

    # Create a single, wider figure for direct overlay comparison
    fig, ax = plt.subplots(figsize=(10, 6))

    # --- Plot P99 (The "Smoking Gun" Metric) ---
    # Solid blue line for Isolated, Dashed red line for Shared
    ax.plot(df_isolated['noise_target_rps'], df_isolated['p99_ms'], 
            marker='o', linestyle='-', color='#4C72B0', linewidth=2.5, markersize=8, label='Sidecar P99 (Isolated)')
    
    ax.plot(df_shared['noise_target_rps'], df_shared['p99_ms'], 
            marker='X', linestyle='--', color='#C44E52', linewidth=2.5, markersize=8, label='Sidecarless P99 (Shared)')

    # Formatting and Labels
    ax.set_title('Isolation Deficit: Sidecar vs Sidecarless Tail Latency (P99)', fontsize=16, pad=15, fontweight='bold')
    ax.set_xlabel('Attacker Noise (Requests Per Second)', fontsize=12, fontweight='bold')
    ax.set_ylabel('Latency (ms)', fontsize=12, fontweight='bold')
    
    # Enhance the grid and legend
    ax.grid(True, linestyle='--', alpha=0.6)
    ax.legend(title='Architecture', title_fontsize='11', fontsize='10', loc='upper left')

    plt.tight_layout()

    # Save the chart under results/graphs so outputs stay with experiment artifacts.
    output_dir = repo_root / "ebpf_research" / "results" / "graphs"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_img = output_dir / "p99_latency_degradation_overlay.png"
    plt.savefig(output_img, dpi=300, bbox_inches='tight')
    
    print(f"Success! Graph saved as: {output_img}")

if __name__ == "__main__":
    main()