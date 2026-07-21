#!/usr/bin/env bash
# restore_env.sh
# Restores CPU environment after testing

if [ "$EUID" -ne 0 ]; then echo "ERROR: Must be root."; exit 1; fi

echo "Restoring all governors to 'ondemand'..."
for gov in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    echo "ondemand" > "$gov"
done

echo "Re-enabling deep C-states..."
cpupower idle-set -E >/dev/null 2>&1 || echo "Warning: cpupower failed to set C-states"

echo "Environment restored."
