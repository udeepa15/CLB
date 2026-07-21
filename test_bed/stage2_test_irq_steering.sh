#!/usr/bin/env bash
# stage2_test_irq_steering.sh
# Validates that we can successfully steer eno6 IRQs to Core 2.

IFACE="eno6"
CORE="2"
# Core 2 in a bitmask is 1 << 2 = 4
BITMASK="4"

echo "=== IRQ Steering Test for $IFACE to Core $CORE ==="

if [ "$EUID" -ne 0 ]; then echo "ERROR: Must be root."; exit 1; fi

# 1. Find the IRQs for the interface
echo "Discovering IRQs for $IFACE..."
IRQS=$(grep -i "$IFACE" /proc/interrupts | awk '{print $1}' | sed 's/://')

if [ -z "$IRQS" ]; then
    echo "No IRQs found for $IFACE"
    exit 1
fi

echo "Found IRQs: $IRQS"

# 2. Record before counts on Core 2 (Column 3 in /proc/interrupts usually, but varies. We'll just look at the whole line)
echo -e "\nBefore steering (Look at CPU2 column):"
grep -i "$IFACE" /proc/interrupts

# 3. Apply steering
echo -e "\nApplying SMP Affinity (bitmask $BITMASK) to IRQs..."
for irq in $IRQS; do
    echo "$BITMASK" > "/proc/irq/$irq/smp_affinity" 2>/dev/null || echo "Failed to set affinity for IRQ $irq"
done

# 4. Generate some traffic
echo -e "\nGenerating traffic (pinging 10.0.0.1 for 3 seconds)..."
ping -c 30 -i 0.1 10.0.0.1 >/dev/null 2>&1 || true

# 5. Record after counts
echo -e "\nAfter steering (Check if CPU2 count incremented):"
grep -i "$IFACE" /proc/interrupts

echo "=== Test Complete ==="
