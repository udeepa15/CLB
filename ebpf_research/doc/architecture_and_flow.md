# eBPF Research Setup Diagrams

This document captures the benchmark architecture and the end-to-end execution flow used by the noisy-neighbor experiments.

## Architecture Diagram

```mermaid
flowchart LR
    subgraph Host[Host Machine]
        direction LR

        subgraph Setup[Setup and Orchestration]
            setup_script[setup.sh]
            run_script[run_benchmarks.sh\nsidecar_vs_sidecarless/run_noise_sweep.sh]
            analysis_scripts[analysis/analyze_fortio.py\nanalysis/plot_metrics.py\nanalysis/generate_report.py]
        end

        subgraph Kernel[Linux Kernel]
            bridge[Bridge: mesh0 / 10.200.0.1]
            veth_victim[veth_vic*_h]
            veth_attacker[veth_att_h]
            clsact[tc clsact qdisc]
            ebpf_isolated[counter_tc_isolated.o]
            ebpf_shared[counter_tc_shared.o]
            bpffs[/sys/fs/bpf/ebpf_research/]
        end

        subgraph Results[Results]
            raw_json[results/raw/*.json]
            raw_logs[results/raw/*.log]
            metrics_csv[results/metrics.csv\nresults/sidecar_vs_sidecarless_metrics.csv]
            graphs[results/graphs/*.png\nresults/graphs/*.csv\nresults/graphs/*.md]
        end
    end

    subgraph VictimNS[victim_ns / victim bundle]
        victim_ct[victim runc container]
        victim_service[busybox nc -lk -p 8080\n/bin/http-echo.sh]
    end

    subgraph AttackerNS[attacker_ns / attacker bundle]
        attacker_ct[attacker runc container]
        attacker_noise[wrk2 or Fortio noise generator]
    end

    setup_script --> bridge
    setup_script --> veth_victim
    setup_script --> veth_attacker
    setup_script --> ebpf_isolated
    setup_script --> ebpf_shared
    setup_script --> bpffs

    bridge --- veth_victim
    bridge --- veth_attacker
    veth_victim --> victim_ct
    veth_attacker --> attacker_ct

    victim_ct --> victim_service
    attacker_ct --> attacker_noise

    clsact --> ebpf_isolated
    clsact --> ebpf_shared
    veth_victim --> clsact
    veth_attacker --> clsact
    ebpf_shared --> bpffs

    run_script --> victim_ct
    run_script --> attacker_ct
    run_script --> raw_json
    run_script --> raw_logs
    run_script --> metrics_csv

    analysis_scripts --> raw_json
    analysis_scripts --> metrics_csv
    analysis_scripts --> graphs

    victim_service -- HTTP request/response latency --> raw_json
    attacker_noise -- background load / noisy-neighbor traffic --> clsact
    ebpf_isolated -- per-interface map key --> victim_ct
    ebpf_isolated -- per-interface map key --> attacker_ct
    ebpf_shared -- shared map entry --> victim_ct
    ebpf_shared -- shared map entry --> attacker_ct
```

## Sequence Diagram

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Setup as setup.sh
    participant Kernel as Host kernel / netns
    participant Runc as runc
    participant Victim as victim container
    participant Attacker as attacker container
    participant EBPF as tc eBPF program
    participant Fortio as Fortio client
    participant Wrk2 as wrk2 noise generator
    participant Raw as results/raw
    participant Analysis as analysis scripts
    participant Graphs as results/graphs

    User->>Setup: Run benchmark setup
    Setup->>Kernel: Create bridge, veth pairs, namespaces, bpffs
    Setup->>Setup: Generate bundle configs and compile eBPF objects

    User->>Runc: Start benchmark run
    Runc->>Victim: Launch victim bundle
    Runc->>Attacker: Launch attacker bundle
    Victim-->>Kernel: Listen on 10.200.0.2:8080

    alt Baseline configuration
        Runc->>Kernel: Remove tc filters / keep no eBPF attached
    else Sidecar isolation
        Runc->>EBPF: Attach counter_tc_isolated.o to victim and attacker veth ingress
    else Sidecarless contention
        Runc->>Kernel: Load counter_tc_shared.o into bpffs
        Runc->>EBPF: Attach the same pinned program to both interfaces
    end

    par Victim measurement
        Fortio->>Victim: Send HTTP requests to 10.200.0.2:8080
        Victim-->>Fortio: HTTP responses with measured latency
    and Noise generation
        Wrk2->>Attacker: Generate background traffic at target RPS
        Attacker-->>EBPF: Ingress packets hit the tc program
        EBPF-->>EBPF: Update packet_count map
    end

    Fortio->>Raw: Write Fortio JSON with DurationHistogram
    Fortio->>Raw: Write run log / summary metrics
    Runc->>Raw: Append p50, p95, p99, throughput, attacker RPS to CSV

    User->>Analysis: Run analysis scripts
    Analysis->>Raw: Read Fortio JSON and metrics CSV
    Analysis->>Graphs: Produce percentile plots, summary CSVs, and report markdown

    Graphs-->>User: Visualizations and experiment report
```

## Component Diagram

```mermaid
graph TB
    subgraph HostMachine[Host Machine - Linux System]
        subgraph Orchestration["Orchestration Layer"]
            SetupSh["setup.sh<br/>(Network & eBPF init)"]
            BenchmarkSh["run_benchmarks.sh<br/>run_noise_sweep.sh<br/>(Benchmark runner)"]
            AnalysisSuite["Analysis Suite<br/>(Python scripts)"]
        end

        subgraph NetworkKernel["Network & Kernel Layer"]
            Bridge["Bridge: mesh0<br/>10.200.0.1/24"]
            Veth1["veth_vic*_h<br/>(to victim)"]
            Veth2["veth_att_h<br/>(to attacker)"]
            TC["tc (Traffic Control)<br/>clsact qdisc"]
            EBPF["eBPF Programs<br/>counter_tc_isolated.o<br/>counter_tc_shared.o"]
            BPFMap["Shared/Isolated<br/>packet_count maps<br/>/sys/fs/bpf/"]
        end

        subgraph DataPipeline["Data & Results"]
            RawResults["Raw Results<br/>results/raw/<br/>*.json, *.log"]
            MetricsCSV["Metrics CSV<br/>metrics.csv<br/>sidecar_vs_*.csv"]
            Graphs["Output Graphs<br/>results/graphs/<br/>*.png, *.csv, *.md"]
        end
    end

    subgraph Containers["Container Runtime (runc)"]
        subgraph VictimContainer["Victim Container<br/>victim_ns"]
            VictimService["HTTP Service<br/>busybox nc<br/>:8080"]
        end

        subgraph AttackerContainer["Attacker Container<br/>attacker_ns"]
            AttackerProcess["Noise Generator<br/>wrk2 / Fortio<br/>@target RPS"]
        end
    end

    subgraph ExternalTools["External Load Testing Tools"]
        Fortio["Fortio Client<br/>(HTTP load generator<br/>latency measurer)"]
        Wrk2["wrk2<br/>(High-perf<br/>load generator)"]
    end

    SetupSh -->|Creates| Bridge
    SetupSh -->|Creates| Veth1
    SetupSh -->|Creates| Veth2
    SetupSh -->|Compiles & Loads| EBPF
    SetupSh -->|Mounts| BPFMap

    Bridge -->|Connects| Veth1
    Bridge -->|Connects| Veth2
    Veth1 -->|Routes to| VictimService
    Veth2 -->|Routes to| AttackerProcess

    TC -->|Attaches| EBPF
    Veth1 -->|Ingress traffic| TC
    Veth2 -->|Ingress traffic| TC
    EBPF -->|Updates| BPFMap

    BenchmarkSh -->|Starts| VictimContainer
    BenchmarkSh -->|Starts| AttackerContainer
    BenchmarkSh -->|Orchestrates| Fortio
    BenchmarkSh -->|Orchestrates| Wrk2

    Fortio -->|HTTP requests| VictimService
    VictimService -->|Responses + latency| Fortio
    Fortio -->|Records| RawResults

    Wrk2 -->|Load traffic| AttackerProcess
    AttackerProcess -->|Packets - contention| EBPF
    BenchmarkSh -->|Extracts metrics| MetricsCSV

    AnalysisSuite -->|Reads| RawResults
    AnalysisSuite -->|Reads| MetricsCSV
    AnalysisSuite -->|Produces| Graphs

    style HostMachine fill:#e1f5ff
    style Containers fill:#fff3e0
    style ExternalTools fill:#f3e5f5
    style Orchestration fill:#c8e6c9
    style NetworkKernel fill:#ffccbc
    style DataPipeline fill:#b3e5fc
```

## What This Setup Measures

The diagrams above represent a benchmark where:

- The victim container serves HTTP on port 8080.
- Fortio measures request latency against that victim endpoint.
- The attacker container generates background traffic to create contention.
- The tc eBPF program counts packets on ingress and creates either isolated or shared map contention.
- The scripts record raw Fortio JSON, extract percentile latency values, and generate plots and summary tables.
