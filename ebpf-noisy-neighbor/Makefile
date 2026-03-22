SHELL := /usr/bin/env bash

.PHONY: setup baseline ebpf suite research plot clean

setup:
	sudo ./environments/base-setup.sh
	sudo ./containers/setup-rootfs.sh

baseline:
	sudo ./experiments/baseline/run.sh

ebpf:
	sudo ./experiments/ebpf-enabled/run.sh

suite:
	sudo ./scripts/run-all-experiments.sh ./config.yaml

research:
	sudo ./scripts/run-all-experiments.sh ./configs/research_matrix.yaml
	python3 analysis/plot.py

plot:
	python3 analysis/plot.py

clean:
	sudo ./scripts/stop-containers.sh
	sudo ./ebpf/tc/attach.sh detach || true
