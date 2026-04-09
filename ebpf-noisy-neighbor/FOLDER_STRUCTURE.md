# Project Folder Structure and Purpose

This document explains the structure of the project and the purpose of each main folder and file.

## Top-Level Structure

- **LICENSE**: Project license information.
- **README.md**: Main project overview and instructions.

## Folders and Their Contents

### ebpf-noisy-neighbor/
Contains the main code and configuration for the eBPF noisy neighbor experiments.
- **config.yaml**: Main configuration file for the project.
- **Makefile**: Build automation for the project.
- **README.md**: Documentation specific to this module.
- **analysis/**: Scripts for analyzing experiment results.
  - **plot.py**: Python script for plotting results.
- **configs/**: Experiment configuration files.
  - **research_matrix.yaml**: Matrix of research experiment parameters.
- **containers/**: Container setup scripts and root filesystem templates.
  - **setup-rootfs.sh**: Script to set up root filesystems.
  - **alpine-rootfs/**: Alpine Linux root filesystem template and configs.
    - **rootfs-template/**: Template for container root filesystems.
    - **configs/**: JSON configs for different tenants and noisy containers.
    - **runtime/**: Runtime configs and rootfs for each container.
- **core/**: Core Python scripts for experiment control and data handling.
  - **adaptive_controller.py**: Adaptive controller logic.
  - **bpf_map_ctl.py**: BPF map control utilities.
  - **finalize_results.py**: Final result processing script.
- **docs/**: Documentation for the project.
  - **EXPERIMENTS.md**: Details about experiments.
  - **GETTING_STARTED.md**: Setup and getting started guide.
  - **TROUBLESHOOTING.md**: Troubleshooting tips.
- **ebpf/**: eBPF programs and related scripts.
  - **tc/**: Traffic control eBPF programs and scripts.
    - **adaptive.c, dropper.c, limiter.c, priority.c, rate_limit.c**: eBPF C source files.
    - **attach.sh, build.sh**: Scripts to attach/build eBPF programs.
- **environments/**: Environment setup scripts and configs.
  - **base-setup.sh**: Base environment setup script.
  - **sysctl.conf**: System configuration for experiments.
- **experiments/**: Experiment execution scripts.
  - **baseline/**, **ebpf-enabled/**: Different experiment scenarios, each with a `run.sh` script.
- **networking/**: Network setup and teardown scripts.
  - **setup-network.sh, teardown-network.sh**: Scripts to configure/clean up networking.
- **results/**: Experiment results.
  - **processed/**: Processed result files (e.g., summary.csv).
  - **raw/**: Raw result data from experiments.
- **scripts/**: Utility scripts for running and managing experiments.
  - **collect-metrics.sh, generate-runtime.sh, run_experiment_matrix.py, run-all-experiments.sh, run-single-experiment.sh, start-containers.sh, stop-containers.sh**: Scripts for automation and orchestration.
- **workloads/**: Workload scripts for different experiment roles.
  - **client.sh, noisy.sh, victim.sh**: Scripts to simulate different types of workloads.

### phase_1/
Contains scripts and resources for phase 1 of the project.
- **netwk_env_ipt.sh**: Network environment setup script.
- **runc-demo/**: Demo container setups for phase 1.
  - **backend1/**, **backend2/**, **client/**, **ebpf-lb/**: Example container root filesystems and eBPF load balancer code.

---

This structure is designed to separate concerns, making it easier to manage experiments, code, configurations, and results.