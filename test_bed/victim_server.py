#!/usr/bin/env python3
"""
victim_server.py — Multi-protocol victim server for test_bed container workloads.
Supports: http, tcp, udp, grpc (via fortio or native socket fallback).
Usage: python3 victim_server.py <protocol> [port]
"""

import sys
import os
import socket
import socketserver
import http.server
import subprocess

PROTOCOL = sys.argv[1].lower() if len(sys.argv) > 1 else "http"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else (
    80 if PROTOCOL == "http" else (
        8079 if PROTOCOL == "grpc" else 8078
    )
)

def run_http(port):
    class QuietHTTPHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Suppress request logging for low latency/overhead

    server_address = ('', port)
    httpd = http.server.HTTPServer(server_address, QuietHTTPHandler)
    print(f"[victim_server] Serving HTTP on port {port}...")
    httpd.serve_forever()

def run_tcp(port):
    class ThreadedTCPHandler(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                while True:
                    data = self.request.recv(1024)
                    if not data:
                        break
                    self.request.sendall(data)
            except Exception:
                pass

    class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
        allow_reuse_address = True

    server = ThreadedTCPServer(('0.0.0.0', port), ThreadedTCPHandler)
    print(f"[victim_server] Serving TCP echo on port {port}...")
    server.serve_forever()

def run_udp(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('0.0.0.0', port))
    print(f"[victim_server] Serving UDP echo on port {port}...")
    while True:
        try:
            data, addr = sock.recvfrom(2048)
            if data and addr:
                sock.sendto(data, addr)
        except Exception:
            pass

def run_grpc(port):
    print(f"[victim_server] Starting fortio gRPC server on port {port}...")
    cmd = [
        "fortio", "server",
        "-grpc-port", str(port),
        "-http-port", "disabled",
        "-tcp-port", "disabled",
        "-udp-port", "disabled",
        "-redirect-port", "disabled"
    ]
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        print("[victim_server] Fortio binary not found inside container, falling back to python TCP/gRPC stub.")
        run_tcp(port)

if __name__ == "__main__":
    if PROTOCOL == "http":
        run_http(PORT)
    elif PROTOCOL == "tcp":
        run_tcp(PORT)
    elif PROTOCOL == "udp":
        run_udp(PORT)
    elif PROTOCOL == "grpc":
        run_grpc(PORT)
    else:
        print(f"Unknown protocol: {PROTOCOL}. Defaulting to HTTP.")
        run_http(80)
