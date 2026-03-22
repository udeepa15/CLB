#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STRATEGY="${1:-dropper}"

case "$STRATEGY" in
  dropper|rate_limit|priority|adaptive)
    ;;
  *)
    echo "[ebpf-build] Unsupported strategy: $STRATEGY"
    echo "[ebpf-build] Use one of: dropper, rate_limit, priority, adaptive"
    exit 1
    ;;
esac

SRC="$SCRIPT_DIR/${STRATEGY}.c"
OUT="$SCRIPT_DIR/${STRATEGY}.o"

ARCH="x86"
INCLUDE_DIR="/usr/include/$(uname -m)-linux-gnu"

echo "[ebpf-build] Compiling $SRC -> $OUT"
clang \
  -O2 -g -target bpf \
  -D__TARGET_ARCH_${ARCH} \
  -I/usr/include \
  -I"$INCLUDE_DIR" \
  -c "$SRC" -o "$OUT"

echo "[ebpf-build] Done."
