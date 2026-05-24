#!/usr/bin/env python3
"""
Seed tenant queues with a specified total number of items distributed evenly.
Usage: seed_queues.py --broker-ip 10.200.0.1 --num-queues 5 --total-items 1000000
"""
from __future__ import annotations
import argparse
import math
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
    p.add_argument("--num-queues", type=int, required=True)
    p.add_argument("--total-items", type=int, required=True)
    p.add_argument("--port", type=int, default=6379)
    return p.parse_args()


def main():
    args = parse_args()
    r = redis.Redis(host=args.broker_ip, port=args.port, socket_timeout=10)
    per = args.total_items // args.num_queues
    extra = args.total_items % args.num_queues
    print(f"Seeding {args.total_items} items across {args.num_queues} queues ({per} each + {extra} extra).")
    for i in range(1, args.num_queues + 1):
        n = per + (1 if i <= extra else 0)
        qname = f"tenant_queue_v{i}"
        batch = 1000
        t0 = time.time()
        pushed = 0
        while pushed < n:
            cnt = min(batch, n - pushed)
            pipe = r.pipeline()
            for _ in range(cnt):
                pipe.rpush(qname, "x")
            pipe.execute()
            pushed += cnt
        print(f"Seeded {n} items into {qname} (elapsed {time.time()-t0:.2f}s)")


if __name__ == "__main__":
    main()
