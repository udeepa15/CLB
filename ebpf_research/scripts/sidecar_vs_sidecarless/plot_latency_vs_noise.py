#!/usr/bin/env python3
import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

CONFIG_LABELS = {
    "sidecar_isolation": "Sidecar",
    "sidecarless_contention": "Sidecarless",
}

CONFIG_COLORS = {
    "sidecar_isolation": "#1f77b4",
    "sidecarless_contention": "#d62728",
}

LATENCY_COLUMNS = [
    ("p50_ms", "P50 Latency vs Noise"),
    ("p95_ms", "P95 Latency vs Noise"),
    ("p99_ms", "P99 Latency vs Noise"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create p50/p95/p99 latency graphs for sidecar vs sidecarless across noise levels."
    )
    parser.add_argument(
        "--input",
        default="ebpf_research/results/sidecar_vs_sidecarless_metrics.csv",
        help="Path to input metrics CSV",
    )
    parser.add_argument(
        "--output-dir",
        default="ebpf_research/results/graphs/sidecar_vs_sidecarless",
        help="Directory to write output PNG files",
    )
    return parser.parse_args()


def load_data(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)

    needed_cols = {
        "config",
        "noise_target_rps",
        "p50_ms",
        "p95_ms",
        "p99_ms",
    }
    missing = needed_cols.difference(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    df = df[df["config"].isin(CONFIG_LABELS.keys())].copy()
    if df.empty:
        raise ValueError("No sidecar/sidecarless rows found in input CSV.")

    for col in ["noise_target_rps", "p50_ms", "p95_ms", "p99_ms"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["noise_target_rps", "p50_ms", "p95_ms", "p99_ms"])
    return df


def aggregate(df: pd.DataFrame, latency_col: str) -> pd.DataFrame:
    grouped = (
        df.groupby(["config", "noise_target_rps"], as_index=False)
        .agg(
            mean=(latency_col, "mean"),
            std=(latency_col, "std"),
            count=(latency_col, "count"),
        )
    )
    grouped["std"] = grouped["std"].fillna(0.0)
    return grouped


def make_plot(grouped: pd.DataFrame, latency_col: str, title: str, output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(9, 5))

    for config in ["sidecar_isolation", "sidecarless_contention"]:
        sub = grouped[grouped["config"] == config].sort_values("noise_target_rps")
        if sub.empty:
            continue

        x = sub["noise_target_rps"].to_numpy()
        y = sub["mean"].to_numpy()
        s = sub["std"].to_numpy()

        ax.plot(
            x,
            y,
            marker="o",
            linewidth=2.0,
            color=CONFIG_COLORS[config],
            label=CONFIG_LABELS[config],
        )
        ax.fill_between(
            x,
            y - s,
            y + s,
            color=CONFIG_COLORS[config],
            alpha=0.18,
        )

    ax.set_title(title)
    ax.set_xlabel("Noise Target RPS (gradual increase)")
    ax.set_ylabel(f"{latency_col.replace('_ms', '').upper()} latency (ms)")
    ax.grid(True, linestyle="--", alpha=0.4)
    ax.legend(title="Configuration")
    plt.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)

    if not input_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_path}")

    df = load_data(input_path)

    for latency_col, title in LATENCY_COLUMNS:
        grouped = aggregate(df, latency_col)
        output_path = output_dir / f"{latency_col}_vs_noise.png"
        make_plot(grouped, latency_col, title, output_path)
        print(f"Saved: {output_path}")


if __name__ == "__main__":
    main()
