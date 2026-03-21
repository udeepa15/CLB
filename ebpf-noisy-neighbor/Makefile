SHELL := /usr/bin/env bash

.PHONY: setup baseline ebpf clean

setup:
	sudo ./environments/base-setup.sh
	sudo ./containers/setup-rootfs.sh

baseline:
	sudo ./experiments/baseline/run.sh

ebpf:
	sudo ./experiments/ebpf-enabled/run.sh

clean:
	sudo ./scripts/stop-containers.sh
	sudo ./ebpf/tc/attach.sh detach || true
