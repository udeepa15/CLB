#!/usr/bin/env python3
"""
retry_client.py — Naive Zero-Backoff Multithreaded HTTP Retry Client for Workload 06.

Executes target QPS HTTP load against victim server with a NAIVE client retry policy:
On HTTP 503 Service Unavailable or Connection Timeout, IMMEDIATELY retries without
backoff or limit until successful HTTP 200 response is obtained.

Outputs Fortio-compatible JSON containing latency percentiles (P50, P90, P99, P999)
and total request counts (for calculating amplification factor).
"""

import sys
import time
import json
import math
import argparse
import urllib.request
import urllib.error
import concurrent.futures

def send_request_with_naive_retries(url, timeout=0.5):
    """Sends a single intended request, retrying with ZERO backoff on 503/timeout until HTTP 200."""
    attempts = 0
    start_time = time.time()
    
    while True:
        attempts += 1
        req_start = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "NaiveRetryClient/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                if response.status == 200:
                    latency_sec = time.time() - req_start
                    total_latency_sec = time.time() - start_time
                    return {
                        "status": 200,
                        "attempts": attempts,
                        "latency_sec": latency_sec,
                        "total_latency_sec": total_latency_sec
                    }
        except urllib.error.HTTPError as e:
            if e.code == 503:
                pass  # Naive retry: immediate retry on 503
        except Exception:
            pass  # Naive retry: immediate retry on timeout/error

def run_retry_workload(target_url, target_qps=50, duration_sec=10, conns=10, out_json="fortio_retry.json"):
    total_intended_requests = int(target_qps * duration_sec)
    interval = 1.0 / float(target_qps)

    print(f"Starting Naive Retry Client: URL={target_url}, Target QPS={target_qps}, Duration={duration_sec}s, Concurrency={conns}...")

    results = []
    start_time = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=conns) as executor:
        futures = []
        for i in range(total_intended_requests):
            scheduled_time = start_time + i * interval
            now = time.time()
            sleep_needed = scheduled_time - now
            if sleep_needed > 0:
                time.sleep(sleep_needed)

            f = executor.submit(send_request_with_naive_retries, target_url)
            futures.append(f)

        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            if res:
                results.append(res)

    total_time = time.time() - start_time
    total_attempts = sum(r["attempts"] for r in results)
    actual_qps = len(results) / total_time if total_time > 0 else 0.0
    amplification_factor = float(total_attempts) / float(len(results)) if len(results) > 0 else 1.0

    # Calculate latency percentiles in seconds
    latencies = sorted([r["total_latency_sec"] for r in results])
    def get_pct(pct):
        if not latencies:
            return 0.0
        idx = int(math.ceil((pct / 100.0) * len(latencies))) - 1
        return latencies[max(0, min(idx, len(latencies) - 1))]

    p50 = get_pct(50)
    p90 = get_pct(90)
    p99 = get_pct(99)
    p999 = get_pct(99.9)

    # Format JSON output to match Fortio parser structure
    fortio_data = {
        "RunType": "HTTP Naive Retry Workload",
        "ActualQPS": actual_qps,
        "TotalAttempts": total_attempts,
        "IntendedRequests": len(results),
        "AmplificationFactor": amplification_factor,
        "DurationHistogram": {
            "Count": len(results),
            "Min": latencies[0] if latencies else 0.0,
            "Max": latencies[-1] if latencies else 0.0,
            "Percentiles": [
                {"Percentile": 50, "Value": p50},
                {"Percentile": 90, "Value": p90},
                {"Percentile": 99, "Value": p99},
                {"Percentile": 99.9, "Value": p999}
            ]
        }
    }

    with open(out_json, "w") as f:
        json.dump(fortio_data, f, indent=2)

    print(f"Completed Naive Retry Workload! Total Intended: {len(results)}, Total Attempted: {total_attempts}, Amplification Factor: {amplification_factor:.2f}x")
    return fortio_data

def main():
    parser = argparse.ArgumentParser(description="Naive Zero-Backoff HTTP Retry Load Generator")
    parser.add_argument("url", type=str, help="Target URL (e.g. http://10.0.0.10:8080/)")
    parser.add_argument("--qps", type=int, default=50, help="Target QPS")
    parser.add_argument("--duration", type=int, default=10, help="Duration in seconds")
    parser.add_argument("--conns", type=int, default=10, help="Concurrent workers")
    parser.add_argument("--out", type=str, default="retry_fortio_out.json", help="Output JSON path")
    args = parser.parse_args()

    run_retry_workload(args.url, args.qps, args.duration, args.conns, args.out)

if __name__ == "__main__":
    main()
