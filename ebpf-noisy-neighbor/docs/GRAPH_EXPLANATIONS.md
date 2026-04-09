# Graph Explanations and Test Configurations

This file explains what each graph means, how each value is computed, and which test configurations are included for that graph.

Primary plotting source:
- [../analysis/plot.py](../analysis/plot.py)

Primary data sources:
- [../results/processed/results.csv](../results/processed/results.csv)
- [../results/processed/summary.csv](../results/processed/summary.csv)
- [../results/raw](../results/raw)
- [../config.yaml](../config.yaml)

## 1) Overhead vs Performance Trade-off

Output file:
- [../results/processed/overhead_vs_performance.png](../results/processed/overhead_vs_performance.png)

Code path:
- [plot_overhead_tradeoff() in ../analysis/plot.py](../analysis/plot.py#L164)

What the graph means:
- Each point is one isolation method (none, tc, ebpf, adaptive).
- X-axis is average packet drops for that method across all included rows.
- Y-axis is average p99 latency for that method across all included rows.
- It visualizes the trade-off between aggressiveness (drops) and tail latency.

Computation details:
- For each row in [../results/processed/results.csv](../results/processed/results.csv):
  - group by isolation_method
  - append packet_drops and p99_ms to method buckets
- Plot one point per method with:
  - $x = mean(packet\_drops)$
  - $y = mean(p99\_ms)$

Test configuration coverage for this graph:
- Included dimension values come from all rows in [../results/processed/results.csv](../results/processed/results.csv):
  - noise_level: low, medium, high
  - traffic_pattern: constant, bursty, delayed_start, intermittent
  - isolation_method: none, tc, ebpf, adaptive
  - identity_mode: ip, cgroup
  - container_count: 3, 5, 10
  - failure_mode: none, spike, churn
  - iteration values: all available
  - tenant values: all available
- Current dataset composition snapshot:
  - total rows: 697
  - by method: none 393, tc 104, ebpf 104, adaptive 96

Interpretation guideline:
- Lower-left is better (fewer drops, lower p99).
- If two methods have similar p99 but one has higher drops, that method has higher overhead.

## 2) p99 vs Noise Level

Output file:
- [../results/processed/p99_vs_noise.png](../results/processed/p99_vs_noise.png)

Code path:
- [plot_p99_vs_noise() in ../analysis/plot.py](../analysis/plot.py#L124)

What the graph means:
- Shows how average p99 latency changes from low to medium to high noise.
- One line per isolation method (none, tc, ebpf, adaptive).
- It measures robustness of each method against increasing interference.

Computation details:
- Rows are filtered by method, then grouped by noise_level.
- For each method and each noise bucket:
  - $y = mean(p99\_ms)$
- X order is fixed as: low, medium, high.

Test configuration coverage for this graph:
- Included dimensions:
  - isolation_method: fixed by line (one method per line)
  - noise_level: low, medium, high (x-axis)
- Mixed/aggregated dimensions within each point:
  - traffic_pattern (all available)
  - identity_mode (ip and cgroup)
  - container_count (3, 5, 10)
  - failure_mode (none, spike, churn)
  - iterations and tenants (all available)
- Current row counts per method-noise point:
  - none: low 329, medium 32, high 32
  - tc: low 40, medium 32, high 32
  - ebpf: low 40, medium 32, high 32
  - adaptive: low 32, medium 32, high 32

Interpretation guideline:
- A flatter curve indicates better stability as noise increases.
- Lower y-values are better.

## 3) Isolation Effectiveness (Lower is Better)

Output file:
- [../results/processed/isolation_effectiveness.png](../results/processed/isolation_effectiveness.png)

Code path:
- [plot_isolation_effectiveness() in ../analysis/plot.py](../analysis/plot.py#L150)

What the graph means:
- Compares average tail amplification for each method.
- Tail amplification is defined as:
  - $tail\_amplification = p99 / p50$
- Lower is better because the tail is closer to median latency.

Computation details:
- Group all rows by isolation_method.
- For each method:
  - $bar\_height = mean(tail\_amplification)$

Test configuration coverage for this graph:
- Included dimensions:
  - isolation_method: none, tc, ebpf, adaptive (bar categories)
- Mixed/aggregated dimensions inside each bar:
  - noise_level, traffic_pattern, identity_mode, container_count, failure_mode
  - all iterations and all tenants available in results.csv

Interpretation guideline:
- Shorter bar means better tail-latency control.
- Useful when methods have similar p99 means but very different tail behavior.

## 4) Latency CDF by Isolation Method

Output file:
- [../results/processed/latency_cdf.png](../results/processed/latency_cdf.png)

Code path:
- [plot_latency_cdf() in ../analysis/plot.py](../analysis/plot.py#L80)

What the graph means:
- Empirical CDF of request latencies for each method.
- For each x value, y is the fraction of requests with latency less than or equal to x.
- Lets you compare full latency distribution, not only p99.

Computation details:
- For every row in [../results/processed/results.csv](../results/processed/results.csv):
  - load raw latency samples from:
    - [../results/raw](../results/raw) / "{experiment}_{tenant}.latency"
- Aggregate all sample latencies into four buckets by method:
  - none -> no_isolation curve
  - tc -> tc curve
  - ebpf -> ebpf curve
  - adaptive -> adaptive curve
- For each bucket:
  - sort values
  - compute empirical CDF as $y_i = i / N$

Test configuration coverage for this graph:
- Included dimensions:
  - isolation_method: curve grouping
- Mixed/aggregated dimensions in each curve:
  - noise_level, traffic_pattern, identity_mode, container_count, failure_mode
  - all iterations, all tenants
- Important caveat:
  - because all scenarios are pooled together, this graph gives a global distribution view, not a single-scenario comparison.

Interpretation guideline:
- Curve farther left indicates lower latency overall.
- Separation near high x values reflects tail improvements.

## 5) Time-series Latency

Output file:
- [../results/processed/latency_time_series.png](../results/processed/latency_time_series.png)

Code path:
- [plot_time_series() in ../analysis/plot.py](../analysis/plot.py#L194)

What the graph means:
- Shows request-by-request latency evolution for one selected run.
- Helps inspect transient spikes, oscillations, and stability over time.

Selection logic (important):
- The script picks the first row in [../results/processed/results.csv](../results/processed/results.csv) where isolation_method == adaptive.
- If no adaptive row exists, it falls back to the first row in the file.
- It then loads:
  - [../results/raw](../results/raw) / "{experiment}_{tenant}.timeseries.csv"

Current selected run in your dataset (from first adaptive row):
- experiment: exp_00013_nlow_pconstant_madaptive_idip_c3_fnone_it1
- tenant: tenant1
- noise_level: low
- traffic_pattern: constant
- isolation_method: adaptive
- identity_mode: ip
- containers: 3
- failure_mode: none
- requests: 400

Interpretation guideline:
- Lower baseline and fewer tall spikes indicate better temporal stability.
- Periodic spikes can indicate control-loop reaction, burst handling, or contention events.

## 6) Noisy Neighbor Mitigation with tc/eBPF (legacy/manual chart)

Observed chart title:
- Noisy Neighbor Mitigation with tc/eBPF

Status in current codebase:
- This title is not generated by [../analysis/plot.py](../analysis/plot.py).
- It appears to be an older/manual comparison chart.

Most likely data source:
- [../results/processed/summary.csv](../results/processed/summary.csv)
- Specifically the early rows with scenario values baseline and ebpf and requests=300.
- Matching example rows:
  - baseline tenant1 p99 6.190
  - baseline tenant2 p99 9.824
  - ebpf tenant1 p99 3.800
  - ebpf tenant2 p99 4.423

Likely test configuration behind that chart:
- Baseline script defaults:
  - [../experiments/baseline/run.sh](../experiments/baseline/run.sh)
  - REQUESTS=300, NOISE_LEVEL=medium, CONTAINERS=3, isolation disabled
- eBPF script defaults:
  - [../experiments/ebpf-enabled/run.sh](../experiments/ebpf-enabled/run.sh)
  - REQUESTS=300, NOISE_LEVEL=high, CONTAINERS=3, eBPF enabled
  - STRATEGY default = dropper

Important caution:
- Because baseline and eBPF defaults use different noise levels (medium vs high), this legacy chart is not a strictly controlled apples-to-apples comparison unless those environment values were aligned during execution.

---

## Global matrix configuration reference

Defined in [../config.yaml](../config.yaml):
- iterations: 5
- requests_per_client: 400
- p99_threshold_ms: 8.0
- sample_interval_s: 1.0
- matrix values:
  - noise_level: low, medium, high
  - traffic_pattern: constant, bursty, delayed_start, intermittent
  - isolation_method: none, tc, ebpf, adaptive
  - identity_mode: ip, cgroup
  - container_count: 3, 5, 10
  - ebpf_strategies: dropper, rate_limit, priority
  - failure_mode: none, spike, churn

This matrix explains why most current graphs are aggregate views across many scenario combinations.