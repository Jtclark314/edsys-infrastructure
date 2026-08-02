#!/usr/bin/env python3
"""Idempotently add the two private NetBox Uptime Kuma monitors.

Run only while the Uptime Kuma container is stopped and after taking a
SQLite-consistent private backup.  Provider credentials are never read.
"""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3


DB_PATH = Path(os.getenv("KUMA_DB", "/mnt/media/docker-data/uptime-kuma/kuma.db"))

MONITORS = (
    {
        "name": "NetBox LAN HTTPS",
        "url": "https://netbox.edsys.local/login/",
        "active": 1,
        "description": "Private LAN HTTPS endpoint with the EdSys Caddy CA trusted by Uptime Kuma.",
    },
    {
        "name": "NetBox Tailnet HTTPS",
        "url": "https://netbox.taile832fe.ts.net/login/",
        "active": 0,
        "description": "Enable only after one-time Tailscale enrollment and Tailscale Serve acceptance pass.",
    },
)


def main() -> int:
    if not DB_PATH.is_file():
        raise SystemExit(f"Uptime Kuma database not found: {DB_PATH}")
    connection = sqlite3.connect(DB_PATH)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        notification = connection.execute(
            "SELECT id FROM notification WHERE active=1 ORDER BY is_default DESC, id LIMIT 1"
        ).fetchone()
        if notification is None:
            raise RuntimeError("No active Uptime Kuma notification provider exists")
        notification_id = notification[0]
        for monitor in MONITORS:
            row = connection.execute("SELECT id FROM monitor WHERE name=?", (monitor["name"],)).fetchone()
            if row is None:
                cursor = connection.execute(
                    """
                    INSERT INTO monitor
                      (name, active, user_id, interval, url, type, maxretries,
                       ignore_tls, retry_interval, method, description, parent,
                       expiry_notification, accepted_statuscodes_json)
                    VALUES (?, ?, 1, 60, ?, 'http', 2, 0, 60, 'GET', ?, 1, 1, '[\"200-299\"]')
                    """,
                    (monitor["name"], monitor["active"], monitor["url"], monitor["description"]),
                )
                monitor_id = cursor.lastrowid
            else:
                monitor_id = row[0]
                connection.execute(
                    """
                    UPDATE monitor
                    SET active=?, user_id=1, interval=60, url=?, type='http',
                        maxretries=2, ignore_tls=0, retry_interval=60,
                        method='GET', description=?, parent=1,
                        expiry_notification=1, accepted_statuscodes_json='[\"200-299\"]'
                    WHERE id=?
                    """,
                    (monitor["active"], monitor["url"], monitor["description"], monitor_id),
                )
            connection.execute("DELETE FROM monitor_notification WHERE monitor_id=?", (monitor_id,))
            next_link_id = connection.execute(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM monitor_notification"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO monitor_notification (id, monitor_id, notification_id) VALUES (?, ?, ?)",
                (next_link_id, monitor_id, notification_id),
            )
            print(f"monitor={monitor['name']} id={monitor_id} active={monitor['active']} tls_verification=required")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
