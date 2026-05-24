#!/usr/bin/env python3
"""
Lightweight queue worker for tenant namespaces.
Usage: queue_worker.py --broker-ip 10.200.0.1 --queue-name tenant_queue_1 --duration-sec 60
Outputs a single line on completion:
RESULT: completed=<integer> errors=<integer> duration_sec=<float> throughput_mps=<float>
"""
from __future__ import annotations
import argparse
import sys
import time

try:
    import redis
except Exception:
    print("Missing dependency 'redis'. Install with: pip3 install redis", file=sys.stderr)
    raise


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--broker-ip", required=True)
    p.add_argument("--queue-name", required=True)
    p.add_argument("--duration-sec", type=float, default=60.0)
    p.add_argument("--port", type=int, default=6379)
    return p.parse_args()


def main():
    args = parse_args()
    r = redis.Redis(host=args.broker_ip, port=args.port, socket_timeout=5)
    start = time.monotonic()
    end_at = start + args.duration_sec
    completed = 0
    errors = 0

    # tight single-threaded atomic LPOP loop
    while time.monotonic() < end_at:
        try:
            item = r.lpop(args.queue_name)
            if item:
                completed += 1
            # if no item, continue spinning; queue should be pre-seeded
        except Exception:
            errors += 1
            # back off briefly to avoid tight error-loop
            time.sleep(0.001)

    duration = time.monotonic() - start
    throughput = completed / duration if duration > 0 else 0.0
    # exact formatting required
    print(f"RESULT: completed={completed} errors={errors} duration_sec={duration:.6f} throughput_mps={throughput:.6f}")


if __name__ == "__main__":
    main()
