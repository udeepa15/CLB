#!/usr/bin/env sh
set -eu

TENANT_NAME="${TENANT_NAME:-tenant}"
LISTEN_PORT="${LISTEN_PORT:-8080}"

echo "[victim] Starting Python HTTP server for ${TENANT_NAME} on :${LISTEN_PORT}"

cat >/tmp/server.py <<'PY'
from http.server import BaseHTTPRequestHandler, HTTPServer
import os

TENANT = os.environ.get("TENANT_NAME", "tenant")
PORT = int(os.environ.get("LISTEN_PORT", "8080"))

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = f"{TENANT} ok\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        return

HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
PY

exec python3 /tmp/server.py
