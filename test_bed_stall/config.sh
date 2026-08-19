#!/usr/bin/env bash
# config.sh — Shared Load and Environment Parameters for Test Bed Experiments

# Benchmark Load Parameters (Identical for Sidecar & Sidecarless)
FLOOD_ARR=(0 u500 u200 u50 u20 u5 u2 flood)
FORTIO_QPS=50
FORTIO_CONNS=2
DURATION_SEC=10
WARMUP_SEC=2

# Protocol Options
SUPPORTED_PROTOCOLS=("http" "grpc" "tcp" "udp")

# Port Assignments per Protocol
PORT_HTTP=8080
PORT_GRPC=8079
PORT_TCP=8078
PORT_UDP=8078

# Target IPs for Victims
VICTIM1_IP="10.0.0.10"
VICTIM2_IP="10.0.0.11"
VICTIM3_IP="10.0.0.12"
ATTACKER_IP="10.0.0.20"
