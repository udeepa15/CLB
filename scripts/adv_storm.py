#!/usr/bin/env python3
"""
Adversary storm: push/poll dummy messages to broker at a target rate (msgs/sec).
Usage: adv_storm.py --broker-ip 10.200.0.1 --rate 10000 --duration 60
"""
from __future__ import annotations
import argparse
import time
import sys

try:
    import redis
except Exception:
    print("Missing dependency 'redis'. Install with: pip3 install redis", file=sys.stderr)
    raise


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--broker-ip", required=True)
    p.add_argument("--port", type=int, default=6379)
    p.add_argument("--rate", type=int, default=0)
    p.add_argument("--duration", type=float, default=60.0)
    p.add_argument("--queue-name", default="adv_queue")
    return p.parse_args()


def main():
    args = parse_args()
    r = redis.Redis(host=args.broker_ip, port=args.port, socket_timeout=5)
    start = time.monotonic()
    end = start + args.duration
    batch = 100
    if args.rate <= 0:
        # idle loop
        time.sleep(args.duration)
        return

    # determine sleep per batch
    msgs_per_sec = args.rate
    interval = batch / msgs_per_sec

    while time.monotonic() < end:
        t0 = time.monotonic()
        pipe = r.pipeline()
        for _ in range(batch):
            pipe.lpush(args.queue_name, "x")
        try:
            pipe.execute()
        except Exception:
            # ignore bursts of errors
            pass
        elapsed = time.monotonic() - t0
        to_sleep = interval - elapsed
        if to_sleep > 0:
            time.sleep(to_sleep)


if __name__ == "__main__":
    main()
