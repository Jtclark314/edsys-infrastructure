#!/usr/bin/env python3
"""Submit a Moonlight PIN from private SSH stdin to local Sunshine only."""
import base64
import json
from pathlib import Path
import re
import ssl
import sys
import time
import urllib.request


def main():
    pin = sys.stdin.readline(32).strip()
    if not re.fullmatch(r"[0-9]{4}", pin):
        raise SystemExit("Expected a four-digit PIN on private stdin")
    path = Path.home() / ".config/sunshine/web-admin.json"
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise SystemExit("Sunshine admin recovery file must be private")
    auth = json.loads(path.read_text())
    basic = base64.b64encode(
        (auth["username"] + ":" + auth["password"]).encode()
    ).decode()
    body = json.dumps({"pin": pin, "name": "TTC Work Laptop"}).encode()
    # Sunshine uses its own certificate; the endpoint is fixed to loopback.
    context = ssl._create_unverified_context()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)
    )
    for _ in range(12):
        req = urllib.request.Request(
            "https://127.0.0.1:47990/api/pin", data=body,
            headers={"Authorization": "Basic " + basic,
                     "Content-Type": "application/json"}, method="POST",
        )
        try:
            with opener.open(req, timeout=5) as response:
                result = json.load(response)
            if result.get("status") is True:
                print("Sunshine accepted the pairing request.")
                return
        except Exception:
            # Never print request, response, credentials, or PIN material.
            pass
        time.sleep(2)
    raise SystemExit("Pairing was not accepted; retry with Moonlight pairing open")


if __name__ == "__main__":
    main()
