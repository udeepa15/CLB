#!/usr/bin/env bash
# setup_env.sh
# Locks CPU environment for rigorous testing

if [ "$EUID" -ne 0 ]; then echo "ERROR: Must be root."; exit 1; fi

echo "Setting all governors to 'performance'..."
for gov in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    echo "performance" > "$gov"
done

echo "Disabling deep C-states (C1E, C3, C6)..."
# -D 2 disables states with latency >= C1E
cpupower idle-set -D 2 >/dev/null 2>&1 || echo "Warning: cpupower failed to set C-states"

echo "Environment locked."
