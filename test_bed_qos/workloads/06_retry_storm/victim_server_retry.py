#!/usr/bin/env python3
"""
victim_server_retry.py — Modified Victim Server for Workload 06 (Retry Storm Amplification).

Introduces a configurable 20% failure/delay rate (HTTP 503 Service Unavailable or 100ms artificial delay)
to trigger naive client-side retries and simulate cascading request amplification.

Derived from ~/CLB/test_bed_qos/victim_server.py.
"""

import sys
import time
import random
import http.server
import socketserver

FAIL_PROBABILITY = 0.20  # 20% failure probability to trigger retry storms

class RetryVictimHTTPHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        # Check if this request triggers an artificial delay/failure
        if random.random() < FAIL_PROBABILITY:
            # Simulate 503 Service Unavailable / Slow response
            if random.random() < 0.5:
                self.send_response(503)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"503 Service Unavailable (Retry Storm Stimulus)\n")
                return
            else:
                time.sleep(0.100)  # 100ms delay to trigger client timeout retry

        # Standard 200 OK Response
        body = b"OK\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # Quiet logging

def main():
    if len(sys.argv) < 3:
        print("Usage: python3 victim_server_retry.py <protocol> <port>")
        sys.exit(1)

    proto = sys.argv[1]
    port = int(sys.argv[2])

    if proto.lower() == "http":
        with socketserver.TCPServer(("", port), RetryVictimHTTPHandler) as httpd:
            print(f"Retry-Storm HTTP Victim listening on port {port}...")
            httpd.serve_forever()
    else:
        print(f"Protocol {proto} not implemented for retry storm. Exiting.")

if __name__ == "__main__":
    main()
