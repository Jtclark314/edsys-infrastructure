#!/usr/bin/env python3
"""Configure the private Uptime Kuma phone-alert provider.

Real provider credentials are supplied through the process environment and are
never written to source control. Take a SQLite-consistent backup of the live
Kuma database before using this mutating helper.
"""

import base64
import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from pathlib import Path


DB_PATH = Path(os.getenv("KUMA_DB", "/mnt/media/docker-data/uptime-kuma/kuma.db"))


def required(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def optional_positive_integer(name):
    value = os.getenv(name, "").strip()
    if not value:
        return ""
    try:
        number = int(value)
    except ValueError as exc:
        raise SystemExit(f"{name} must be a positive integer when set") from exc
    if number <= 0:
        raise SystemExit(f"{name} must be a positive integer when set")
    return str(number)


def build_config():
    provider = os.getenv("KUMA_ALERT_PROVIDER", "").strip().lower()
    name = os.getenv("KUMA_ALERT_NAME", "").strip()
    if provider == "pushover":
        return {
            "type": "pushover",
            "name": name or "EdSys Phone Alerts - Pushover",
            "isDefault": True,
            "applyExisting": False,
            "pushoveruserkey": required("PUSHOVER_USER_KEY"),
            "pushoverapptoken": required("PUSHOVER_APP_TOKEN"),
            "pushoverdevice": os.getenv("PUSHOVER_DEVICE", "").strip(),
            "pushovertitle": os.getenv("PUSHOVER_TITLE", "EdSys Alert"),
            "pushoverpriority": os.getenv("PUSHOVER_PRIORITY", "1"),
            "pushoversounds": os.getenv("PUSHOVER_SOUND", "persistent"),
            # Omit TTL by default. Pushover rejects ttl=0; when supplied it
            # must be a strictly positive number of seconds.
            "pushoverttl": optional_positive_integer("PUSHOVER_TTL"),
        }
    if provider == "twilio":
        return {
            "type": "twilio",
            "name": name or "EdSys Phone Alerts - Twilio SMS",
            "isDefault": True,
            "applyExisting": False,
            "twilioAccountSID": required("TWILIO_ACCOUNT_SID"),
            "twilioApiKey": os.getenv("TWILIO_API_KEY", "").strip(),
            "twilioAuthToken": required("TWILIO_AUTH_TOKEN"),
            "twilioFromNumber": required("TWILIO_FROM_NUMBER"),
            "twilioToNumber": required("TWILIO_TO_NUMBER"),
        }
    raise SystemExit("Set KUMA_ALERT_PROVIDER to pushover or twilio")


def bind_notification(conn, notification_id):
    monitor_ids = [
        row[0]
        for row in conn.execute(
            "select id from monitor where active=1 and user_id=1 and type != ? order by id",
            ("group",),
        )
    ]
    conn.execute("delete from monitor_notification where notification_id=?", (notification_id,))
    conn.executemany(
        "insert into monitor_notification (monitor_id, notification_id) values (?, ?)",
        [(monitor_id, notification_id) for monitor_id in monitor_ids],
    )
    return len(monitor_ids)


def save_notification(config):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("update notification set is_default=0")
        row = conn.execute("select id from notification where name=?", (config["name"],)).fetchone()
        config_json = json.dumps(config, separators=(",", ":"))
        if row:
            notification_id = row[0]
            conn.execute(
                "update notification set active=1, user_id=1, is_default=1, config=? where id=?",
                (config_json, notification_id),
            )
        else:
            conn.execute(
                "insert into notification (name, active, user_id, is_default, config) values (?, 1, 1, 1, ?)",
                (config["name"], config_json),
            )
            notification_id = conn.execute("select last_insert_rowid()").fetchone()[0]
        bound_count = bind_notification(conn, notification_id)
        conn.commit()
        return notification_id, bound_count
    finally:
        conn.close()


def post_form(url, data, headers=None):
    encoded = urllib.parse.urlencode(data).encode()
    request = urllib.request.Request(url, data=encoded, headers=headers or {}, method="POST")
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.status, response.read().decode("utf-8", "replace")


def send_test(config):
    message = os.getenv("KUMA_ALERT_TEST_MESSAGE", "EdSys Uptime Kuma phone alerts are configured.")
    if config["type"] == "pushover":
        return post_form(
            "https://api.pushover.net/1/messages.json",
            {
                "token": config["pushoverapptoken"],
                "user": config["pushoveruserkey"],
                "title": config["pushovertitle"],
                "message": message,
                "priority": config["pushoverpriority"],
                "sound": config["pushoversounds"],
            },
        )
    if config["type"] == "twilio":
        api_key = config.get("twilioApiKey") or config["twilioAccountSID"]
        token = config["twilioAuthToken"]
        basic = base64.b64encode(f"{api_key}:{token}".encode()).decode()
        url = f"https://api.twilio.com/2010-04-01/Accounts/{config['twilioAccountSID']}/Messages.json"
        return post_form(
            url,
            {
                "To": config["twilioToNumber"],
                "From": config["twilioFromNumber"],
                "Body": message,
            },
            {"Authorization": f"Basic {basic}"},
        )
    raise RuntimeError("Unsupported provider")


def main():
    config = build_config()
    notification_id, bound_count = save_notification(config)
    print(f"configured={config['type']} notification_id={notification_id} bound_monitors={bound_count}")
    if os.getenv("KUMA_ALERT_SEND_TEST", "").strip() == "1":
        status, _body = send_test(config)
        print(f"test_status={status}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"error={exc}", file=sys.stderr)
        raise
