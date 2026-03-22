#!/usr/bin/env python3
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import yaml


def _normalize_method(method: str, strategy: str, identity_mode: str) -> tuple[str, str, str, str]:
    method = method.strip().lower()
    strategy = strategy.strip().lower()
    identity_mode = identity_mode.strip().lower()

    if method == "none":
        return "none", "false", "none", "ip"

    if method == "tc":
        return "tc", "false", "none", "ip"

    if method == "ebpf":
        if strategy == "adaptive":
            strategy = "rate_limit"
        if strategy not in {"dropper", "rate_limit", "priority"}:
            strategy = "rate_limit"
        return "ebpf", "true", strategy, "ip"

    if method == "adaptive":
        if identity_mode not in {"ip", "cgroup"}:
            identity_mode = "ip"
        return "adaptive", "true", "adaptive", identity_mode

    return "none", "none", "none", "ip"


def emit_entry(
    name: str,
    noise_level: str,
    traffic_pattern: str,
    isolation_method: str,
    identity_mode: str,
    containers: int,
    requests: int,
    iteration: int,
    failure_mode: str,
    p99_threshold_ms: float,
    strategy: str,
) -> None:
    method, ebpf_enabled, resolved_strategy, resolved_identity = _normalize_method(
        isolation_method, strategy, identity_mode
    )

    print(
        "|".join(
            [
                name,
                str(noise_level),
                ebpf_enabled,
                str(containers),
                resolved_strategy,
                str(requests),
                method,
                str(traffic_pattern),
                resolved_identity,
                str(iteration),
                str(failure_mode),
                f"{p99_threshold_ms}",
            ]
        )
    )


def emit_from_matrix(data: dict) -> int:
    global_cfg = data.get("global", {})
    matrix = data.get("matrix", {})

    iterations = int(global_cfg.get("iterations", 1))
    requests = int(global_cfg.get("requests_per_client", 300))
    p99_threshold_ms = float(global_cfg.get("p99_threshold_ms", 10.0))

    noise_levels = matrix.get("noise_level", ["medium"])
    traffic_patterns = matrix.get("traffic_pattern", ["constant"])
    methods = matrix.get("isolation_method", ["none"])
    identities = matrix.get("identity_mode", ["ip"])
    containers = matrix.get("container_count", [3])
    failure_modes = matrix.get("failure_mode", ["none"])
    strategies = matrix.get("ebpf_strategies", ["rate_limit"])

    idx = 0
    for noise, pattern, method, ident, count, failure in itertools.product(
        noise_levels, traffic_patterns, methods, identities, containers, failure_modes
    ):
        strategy_set = ["none"]
        if str(method) == "ebpf":
            strategy_set = list(strategies)
        elif str(method) == "adaptive":
            strategy_set = ["adaptive"]

        for strategy in strategy_set:
            for iteration in range(1, iterations + 1):
                idx += 1
                name = (
                    f"exp_{idx:05d}_n{noise}_p{pattern}_m{method}_"
                    f"id{ident}_c{count}_f{failure}_it{iteration}"
                )
                emit_entry(
                    name=name,
                    noise_level=str(noise),
                    traffic_pattern=str(pattern),
                    isolation_method=str(method),
                    identity_mode=str(ident),
                    containers=int(count),
                    requests=requests,
                    iteration=iteration,
                    failure_mode=str(failure),
                    p99_threshold_ms=p99_threshold_ms,
                    strategy=str(strategy),
                )
    return 0


def emit_from_scenarios(data: dict) -> int:
    scenarios = data.get("scenarios", [])
    if not scenarios:
        return 1

    for i, s in enumerate(scenarios, start=1):
        emit_entry(
            name=str(s.get("name", f"scenario_{i:03d}")),
            noise_level=str(s.get("noise_level", "medium")),
            traffic_pattern=str(s.get("traffic_pattern", "constant")),
            isolation_method=str(s.get("isolation_method", "none")),
            identity_mode=str(s.get("identity_mode", "ip")),
            containers=int(s.get("container_count", 3)),
            requests=int(s.get("requests", 300)),
            iteration=int(s.get("iteration", 1)),
            failure_mode=str(s.get("failure_mode", "none")),
            p99_threshold_ms=float(s.get("p99_threshold_ms", 10.0)),
            strategy=str(s.get("ebpf_strategy", "rate_limit")),
        )
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: run_experiment_matrix.py <config.yaml>", file=sys.stderr)
        return 1

    cfg_path = Path(sys.argv[1])
    if not cfg_path.exists():
        print(f"missing config file: {cfg_path}", file=sys.stderr)
        return 1

    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    # Backward compatibility: explicit experiments list.
    if data.get("experiments"):
        for exp in data.get("experiments", []):
            emit_entry(
                name=str(exp.get("name", "experiment")),
                noise_level=str(exp.get("noise_level", "medium")),
                traffic_pattern=str(exp.get("traffic_pattern", "constant")),
                isolation_method=("ebpf" if bool(exp.get("ebpf", False)) else "none"),
                identity_mode=str(exp.get("identity_mode", "ip")),
                containers=int(exp.get("containers", 3)),
                requests=int(exp.get("requests", 300)),
                iteration=int(exp.get("iteration", 1)),
                failure_mode=str(exp.get("failure_mode", "none")),
                p99_threshold_ms=float(exp.get("p99_threshold_ms", 10.0)),
                strategy=str(exp.get("strategy", "rate_limit")),
            )
        return 0

    if emit_from_scenarios(data) == 0:
        return 0

    if data.get("matrix"):
        return emit_from_matrix(data)

    print("config has no experiments[], scenarios[], or matrix definitions", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
