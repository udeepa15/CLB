#!/usr/bin/env bash
# stage0_discovery.sh

echo "=== STAGE 0: PRE-FLIGHT DISCOVERY ==="
echo ""

echo "--- 1. NUMA Topology ---"
if command -v numactl >/dev/null 2>&1; then
    numactl --hardware
else
    echo "numactl not installed (please run: sudo apt install numactl hwloc)"
fi
echo ""
echo "eno6 NUMA node:"
cat /sys/class/net/eno6/device/numa_node 2>/dev/null || echo "eno6 not found"
echo "enp12s0f1 NUMA node:"
cat /sys/class/net/enp12s0f1/device/numa_node 2>/dev/null || echo "enp12s0f1 not found"
echo ""
if command -v lstopo-no-graphics >/dev/null 2>&1; then
    echo "--- hwloc lstopo ---"
    lstopo-no-graphics --no-legend
else
    echo "hwloc not installed (lstopo unavailable)"
fi
echo ""

echo "--- 2. IRQ Mapping ---"
echo "eno6 interrupts:"
grep -i eno6 /proc/interrupts || echo "None found"
echo ""
echo "enp12s0f1 interrupts:"
grep -i enp12s0f1 /proc/interrupts || echo "None found"
echo ""

echo "--- 3. CPU Power / Idle State ---"
echo "Scaling Governors:"
cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor 2>/dev/null | sort | uniq -c || echo "Not found"
if command -v cpupower >/dev/null 2>&1; then
    echo "cpupower idle-info:"
    cpupower idle-info
else
    echo "cpupower not installed (please run: sudo apt install linux-tools-common linux-tools-generic)"
fi
echo ""

echo "--- 4. Cgroup v2 Delegation ---"
echo "Root controllers:"
cat /sys/fs/cgroup/cgroup.controllers 2>/dev/null || echo "Not found"
echo "Root subtree_control:"
cat /sys/fs/cgroup/cgroup.subtree_control 2>/dev/null || echo "Not found"

echo "=== END STAGE 0 ==="
