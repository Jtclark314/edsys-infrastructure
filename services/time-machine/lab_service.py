"""Disposable rehearsal application. Contains synthetic data only."""
import json
import os
import socket
import sqlite3
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROLE = os.environ.get("LAB_ROLE", "application")
ROOT = Path("/fixture")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        code = 200
        if ROLE == "database":
            try:
                with sqlite3.connect(f"file:{ROOT / 'records.sqlite'}?mode=ro", uri=True) as db:
                    records = db.execute("SELECT id, value FROM records ORDER BY id").fetchall()
                payload = {"state": "ok", "records": records}
            except sqlite3.Error:
                code, payload = 503, {"state": "down", "reason": "storage unavailable"}
        elif os.environ.get("LAB_RELEASE") == "broken":
            code, payload = 503, {"state": "down", "reason": "release health check failed"}
        else:
            try:
                destination = (ROOT / "destination").read_text().strip()
                socket.getaddrinfo(destination, 8080)
                with urllib.request.urlopen(f"http://{destination}:8080", timeout=3) as response:
                    payload = json.load(response)
            except socket.gaierror:
                code, payload = 503, {"state": "down", "reason": "DNS resolution failed"}
            except (OSError, urllib.error.URLError, ValueError):
                code, payload = 503, {"state": "down", "reason": "database unavailable"}
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
