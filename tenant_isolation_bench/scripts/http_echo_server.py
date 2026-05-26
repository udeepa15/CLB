#!/usr/bin/env python3
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tiny fixed-response HTTP server")
    parser.add_argument("--bind", required=True, help="Bind address")
    parser.add_argument("--port", type=int, default=8080, help="Listen port")
    parser.add_argument("--body", default="ok\n", help="Response body")
    parser.add_argument("--content-type", default="text/plain; charset=utf-8")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    body = args.body.encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", args.content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *values) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
