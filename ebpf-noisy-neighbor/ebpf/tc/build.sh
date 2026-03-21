#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$SCRIPT_DIR/limiter.c"
OUT="$SCRIPT_DIR/limiter.o"

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
