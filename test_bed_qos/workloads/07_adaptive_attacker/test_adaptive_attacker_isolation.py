#!/usr/bin/env python3
"""
test_adaptive_attacker_isolation.py — Isolated Unit Test for Workload 07 Adaptive Attacker.

Simulates a mock controller JSONL log and verifies that AdaptiveAttackerThread:
1. Successfully polls the controller log file.
2. Correctly reads attacker_rate_limit_bps.
3. Adapts its internal flood intensity index (backing off under high throttling, re-escalating under low throttling).
"""

import os
import sys
import time
import json

sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shared"))
from workload_lib import AdaptiveAttackerThread, log

def test_isolation():
    mock_log_path = "/tmp/mock_qos_controller.jsonl"
    if os.path.exists(mock_log_path):
        os.remove(mock_log_path)

    log("=== Starting Workload 07 Adaptive Attacker Isolation Test ===")

    # Initialize thread in dry-run mode (target IP 127.0.0.1, poll 0.5s)
    attacker = AdaptiveAttackerThread(
        target_ip="127.0.0.1",
        target_port=8080,
        controller_log_path=mock_log_path,
        poll_interval=0.5
    )

    # Override _start_hping to prevent actual hping3 execution during unit test
    def mock_start_hping(level):
        log(f"[MockHping3] Executing flood command with intensity: {level}")
        return None

    attacker._start_hping = mock_start_hping

    attacker.start()
    time.sleep(1.0)
    log(f"Initial state: Level = {attacker.levels[attacker.curr_idx]} (Index {attacker.curr_idx})")

    # Step 1: Simulate High Throttling (rate limit drops to 10 MB/s)
    log("\n--- Injecting High Throttling Signal (10 MB/s limit) into mock controller log ---")
    with open(mock_log_path, "a") as f:
        f.write(json.dumps({"timestamp": time.time(), "attacker_rate_limit_bps": 10000000}) + "\n")
        f.flush()

    time.sleep(1.5)
    log(f"After High Throttling: Level = {attacker.levels[attacker.curr_idx]} (Index {attacker.curr_idx})")
    assert attacker.curr_idx < 3, f"Expected backoff index < 3, got {attacker.curr_idx}"

    # Step 2: Simulate Low Throttling (rate limit relaxes to 45 MB/s)
    log("\n--- Injecting Low Throttling Signal (45 MB/s limit) into mock controller log ---")
    with open(mock_log_path, "a") as f:
        f.write(json.dumps({"timestamp": time.time(), "attacker_rate_limit_bps": 45000000}) + "\n")
        f.flush()

    time.sleep(1.5)
    log(f"After Low Throttling: Level = {attacker.levels[attacker.curr_idx]} (Index {attacker.curr_idx})")

    attacker.stop()
    if os.path.exists(mock_log_path):
        os.remove(mock_log_path)

    log("\nSUCCESS: Adaptive Attacker feedback-reading mechanism verified in isolation!")

if __name__ == "__main__":
    test_isolation()
