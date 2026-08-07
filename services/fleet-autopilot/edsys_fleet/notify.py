from __future__ import annotations

import os
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_URL_FILE = Path("/etc/edsys-fleet/notify-url")


def notify_critical_failure(*, title: str, message: str) -> dict[str, Any]:
    """Send one bounded notification through a private runtime URL.

    The URL itself never enters Fleet evidence or source control. The file may
    point at the existing private ntfy topic or another approved EdSys webhook.
    Notification failure never hides the benchmark result.
    """

    selected = Path(os.getenv("EDSYS_FLEET_NOTIFY_URL_FILE", str(DEFAULT_URL_FILE)))
    if not selected.is_file():
        return {"configured": False, "delivered": False}
    try:
        url = selected.read_text(encoding="utf-8").strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("notification URL must use HTTP or HTTPS")
        payload = message.encode("utf-8")[:2048]
        request = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "Title": title[:128],
                "Priority": "high",
                "Tags": "warning,computer",
                "User-Agent": "edsys-fleet-autopilot/2",
            },
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            delivered = 200 <= int(getattr(response, "status", 0)) < 300
        return {"configured": True, "delivered": delivered}
    except Exception as exc:  # notification must never mask the primary failure
        return {
            "configured": True,
            "delivered": False,
            "error_type": type(exc).__name__,
        }
