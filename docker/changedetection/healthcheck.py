#!/usr/bin/env python3
"""Check an authenticated API response without printing credentials or state."""

import json
from pathlib import Path
from urllib.request import Request, urlopen


def main():
    settings = json.loads(Path("/datastore/changedetection.json").read_text())["settings"]["application"]
    if settings.get("api_access_token_enabled") is not True:
        raise RuntimeError("API key protection must remain enabled")
    token = settings.get("api_access_token")
    if not token:
        raise RuntimeError("API key is missing")
    request = Request(
        "http://127.0.0.1:5000/api/v1/systeminfo",
        headers={"x-api-key": token},
    )
    with urlopen(request, timeout=5) as response:
        status = json.load(response)
    if not isinstance(status.get("watch_count"), int) or not status.get("version"):
        raise RuntimeError("Unexpected system information response")


if __name__ == "__main__":
    main()
