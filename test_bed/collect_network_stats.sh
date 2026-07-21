#!/usr/bin/env bash
# collect_network_stats.sh
# Usage: ./collect_network_stats.sh <interface> <output_dir> <prefix>

IFACE=$1
OUTDIR=$2
PREFIX=$3

mkdir -p "$OUTDIR"

# 1. softirq / NAPI
echo "=== /proc/softirqs (NET_RX) ===" > "${OUTDIR}/${PREFIX}_network_state.txt"
grep NET_RX /proc/softirqs >> "${OUTDIR}/${PREFIX}_network_state.txt"

# 2. mpstat
if command -v mpstat >/dev/null 2>&1; then
    echo -e "\n=== mpstat -P ALL ===" >> "${OUTDIR}/${PREFIX}_network_state.txt"
    mpstat -P ALL 1 1 >> "${OUTDIR}/${PREFIX}_network_state.txt"
fi

# 3. ip drop counters
echo -e "\n=== ip -s -s link show ${IFACE} ===" >> "${OUTDIR}/${PREFIX}_network_state.txt"
ip -s -s link show "$IFACE" >> "${OUTDIR}/${PREFIX}_network_state.txt"

# 4. nstat drops
echo -e "\n=== nstat drops ===" >> "${OUTDIR}/${PREFIX}_network_state.txt"
nstat -az | grep -Ei 'drop|overrun|backlog' >> "${OUTDIR}/${PREFIX}_network_state.txt"

# 5. ethtool -S
if command -v ethtool >/dev/null 2>&1; then
    echo -e "\n=== ethtool -S ${IFACE} ===" >> "${OUTDIR}/${PREFIX}_network_state.txt"
    ethtool -S "$IFACE" | grep -Ei 'drop|err|fail' >> "${OUTDIR}/${PREFIX}_network_state.txt"
fi
