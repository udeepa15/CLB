#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REQUESTS="${REQUESTS:-300}"
NOISE_LEVEL="${NOISE_LEVEL:-high}"
CONTAINERS="${CONTAINERS:-3}"
STRATEGY="${STRATEGY:-dropper}"

"$ROOT_DIR/scripts/run-single-experiment.sh" \
  "ebpf_manual" \
  "$NOISE_LEVEL" \
  "true" \
  "$CONTAINERS" \
  "$STRATEGY" \
  "$REQUESTS"
