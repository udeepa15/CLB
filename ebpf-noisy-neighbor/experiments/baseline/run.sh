#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REQUESTS="${REQUESTS:-300}"
NOISE_LEVEL="${NOISE_LEVEL:-medium}"
CONTAINERS="${CONTAINERS:-3}"

"$ROOT_DIR/scripts/run-single-experiment.sh" \
  "baseline_manual" \
  "$NOISE_LEVEL" \
  "false" \
  "$CONTAINERS" \
  "none" \
  "$REQUESTS"
