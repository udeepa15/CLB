#!/usr/bin/env python3
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import yaml


def _coerce_int(value: object, default: int, minimum: int | None = None) -> int:
    try:
        out = int(str(value))
    except Exception:
        out = default
    if minimum is not None and out < minimum:
        out = minimum
    return out


def _coerce_str(value: object, default: str) -> str:
    raw = str(value).strip()
    return raw if raw else default


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
    tenant_cpu_quota_pct: int,
    tenant_memory_mb: int,
    tenant_cpuset: str,
    noisy_cpu_quota_pct: int,
    noisy_memory_mb: int,
    noisy_cpuset: str,
    host_reserved_cpus: str,
    host_mem_pressure_mb: int,
    io_read_bps: int,
    io_write_bps: int,
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
                str(tenant_cpu_quota_pct),
                str(tenant_memory_mb),
                str(tenant_cpuset),
                str(noisy_cpu_quota_pct),
                str(noisy_memory_mb),
                str(noisy_cpuset),
                str(host_reserved_cpus),
                str(host_mem_pressure_mb),
                str(io_read_bps),
                str(io_write_bps),
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
    tenant_cpu_quota_pcts = matrix.get("tenant_cpu_quota_pct", [80])
    tenant_memory_mbs = matrix.get("tenant_memory_mb", [512])
    tenant_cpusets = matrix.get("tenant_cpuset", ["auto"])
    noisy_cpu_quota_pcts = matrix.get("noisy_cpu_quota_pct", [60])
    noisy_memory_mbs = matrix.get("noisy_memory_mb", [384])
    noisy_cpusets = matrix.get("noisy_cpuset", ["auto"])
    host_reserved_cpu_sets = matrix.get("host_reserved_cpus", [""])
    host_mem_pressure_mbs = matrix.get("host_mem_pressure_mb", [0])
    io_read_bps_values = matrix.get("io_read_bps", [0])
    io_write_bps_values = matrix.get("io_write_bps", [0])

    idx = 0
    for (
        noise,
        pattern,
        method,
        ident,
        count,
        failure,
        tenant_cpu_quota_pct,
        tenant_memory_mb,
        tenant_cpuset,
        noisy_cpu_quota_pct,
        noisy_memory_mb,
        noisy_cpuset,
        host_reserved_cpus,
        host_mem_pressure_mb,
        io_read_bps,
        io_write_bps,
    ) in itertools.product(
        noise_levels,
        traffic_patterns,
        methods,
        identities,
        containers,
        failure_modes,
        tenant_cpu_quota_pcts,
        tenant_memory_mbs,
        tenant_cpusets,
        noisy_cpu_quota_pcts,
        noisy_memory_mbs,
        noisy_cpusets,
        host_reserved_cpu_sets,
        host_mem_pressure_mbs,
        io_read_bps_values,
        io_write_bps_values,
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
                    f"id{ident}_c{count}_f{failure}_"
                    f"tcq{_coerce_int(tenant_cpu_quota_pct, 80, 1)}_"
                    f"tm{_coerce_int(tenant_memory_mb, 512, 64)}_"
                    f"ncq{_coerce_int(noisy_cpu_quota_pct, 60, 1)}_"
                    f"nm{_coerce_int(noisy_memory_mb, 384, 64)}_"
                    f"hm{_coerce_int(host_mem_pressure_mb, 0, 0)}_"
                    f"it{iteration}"
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
                    tenant_cpu_quota_pct=_coerce_int(tenant_cpu_quota_pct, 80, 1),
                    tenant_memory_mb=_coerce_int(tenant_memory_mb, 512, 64),
                    tenant_cpuset=_coerce_str(tenant_cpuset, "auto"),
                    noisy_cpu_quota_pct=_coerce_int(noisy_cpu_quota_pct, 60, 1),
                    noisy_memory_mb=_coerce_int(noisy_memory_mb, 384, 64),
                    noisy_cpuset=_coerce_str(noisy_cpuset, "auto"),
                    host_reserved_cpus=_coerce_str(host_reserved_cpus, ""),
                    host_mem_pressure_mb=_coerce_int(host_mem_pressure_mb, 0, 0),
                    io_read_bps=_coerce_int(io_read_bps, 0, 0),
                    io_write_bps=_coerce_int(io_write_bps, 0, 0),
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
            tenant_cpu_quota_pct=_coerce_int(s.get("tenant_cpu_quota_pct", 80), 80, 1),
            tenant_memory_mb=_coerce_int(s.get("tenant_memory_mb", 512), 512, 64),
            tenant_cpuset=_coerce_str(s.get("tenant_cpuset", "auto"), "auto"),
            noisy_cpu_quota_pct=_coerce_int(s.get("noisy_cpu_quota_pct", 60), 60, 1),
            noisy_memory_mb=_coerce_int(s.get("noisy_memory_mb", 384), 384, 64),
            noisy_cpuset=_coerce_str(s.get("noisy_cpuset", "auto"), "auto"),
            host_reserved_cpus=_coerce_str(s.get("host_reserved_cpus", ""), ""),
            host_mem_pressure_mb=_coerce_int(s.get("host_mem_pressure_mb", 0), 0, 0),
            io_read_bps=_coerce_int(s.get("io_read_bps", 0), 0, 0),
            io_write_bps=_coerce_int(s.get("io_write_bps", 0), 0, 0),
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
                tenant_cpu_quota_pct=_coerce_int(exp.get("tenant_cpu_quota_pct", 80), 80, 1),
                tenant_memory_mb=_coerce_int(exp.get("tenant_memory_mb", 512), 512, 64),
                tenant_cpuset=_coerce_str(exp.get("tenant_cpuset", "auto"), "auto"),
                noisy_cpu_quota_pct=_coerce_int(exp.get("noisy_cpu_quota_pct", 60), 60, 1),
                noisy_memory_mb=_coerce_int(exp.get("noisy_memory_mb", 384), 384, 64),
                noisy_cpuset=_coerce_str(exp.get("noisy_cpuset", "auto"), "auto"),
                host_reserved_cpus=_coerce_str(exp.get("host_reserved_cpus", ""), ""),
                host_mem_pressure_mb=_coerce_int(exp.get("host_mem_pressure_mb", 0), 0, 0),
                io_read_bps=_coerce_int(exp.get("io_read_bps", 0), 0, 0),
                io_write_bps=_coerce_int(exp.get("io_write_bps", 0), 0, 0),
            )
        return 0

    if emit_from_scenarios(data) == 0:
        return 0

    if data.get("matrix"):
        return emit_from_matrix(data)

    print("config has no experiments[], scenarios[], or matrix definitions", file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        raise SystemExit(0)
