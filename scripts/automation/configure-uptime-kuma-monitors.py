#!/usr/bin/env python3
"""Offline, idempotent Uptime Kuma monitor reconciliation for EdCore Automation.

The Uptime Kuma container must be stopped. A SQLite-consistent private backup
is created before the transaction. No service credential or private CA material
is read or stored by this helper.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import tempfile


DEFAULT_DB_PATH = Path(os.getenv("KUMA_DB", "/mnt/media/docker-data/uptime-kuma/kuma.db"))
DEFAULT_BACKUP_ROOT = Path(
    os.getenv("KUMA_BACKUP_ROOT", "/var/backups/uptime-kuma/edcore-automation-monitors")
)
DEFAULT_CONTAINER = os.getenv("KUMA_CONTAINER", "uptime-kuma")
DEFAULT_PARENT_GROUP = os.getenv("KUMA_PARENT_GROUP", "Voice and Automation")
DEFAULT_TLS_CA_FILE = os.getenv("AUTOMATION_TLS_CA_FILE", "")
AUTOMATION_IP = "192.168.50.82"


@dataclass(frozen=True)
class MonitorSpec:
    name: str
    monitor_type: str
    description: str
    url: str | None = None
    hostname: str | None = None
    port: int | None = None
    accepted_statuses: str = '["200-299"]'


MONITORS = (
    MonitorSpec(
        name="EdCore Automation VM Ping",
        monitor_type="ping",
        hostname=AUTOMATION_IP,
        description="Private LAN reachability for production automation VMID 324.",
    ),
    MonitorSpec(
        name="EdCore Automation MQTT TLS Port",
        monitor_type="port",
        hostname=AUTOMATION_IP,
        port=8883,
        description=(
            "TCP reachability of the TLS-only MQTT listener. Uptime Kuma 1.x does not perform "
            "an MQTT mTLS handshake; stack acceptance separately verifies CA and client identity."
        ),
    ),
    MonitorSpec(
        name="EdCore Automation Node-RED HTTPS",
        monitor_type="http",
        url=f"https://{AUTOMATION_IP}:1880/",
        accepted_statuses='["200-399"]',
        description=(
            "Private Node-RED LAN endpoint; an unauthenticated 2xx/3xx editor/login shell is "
            "expected while editor and Admin API actions remain authenticated."
        ),
    ),
    MonitorSpec(
        name="EdCore Automation InfluxDB Health",
        monitor_type="http",
        url=f"https://{AUTOMATION_IP}:8086/health",
        description="Private InfluxDB health endpoint restricted to the 9950x monitoring source.",
    ),
)


REQUIRED_MONITOR_COLUMNS = {
    "id",
    "name",
    "active",
    "user_id",
    "interval",
    "url",
    "type",
    "hostname",
    "port",
    "maxretries",
    "ignore_tls",
    "retry_interval",
    "method",
    "description",
    "parent",
    "expiry_notification",
    "accepted_statuscodes_json",
    "basic_auth_user",
    "basic_auth_pass",
    "mqtt_username",
    "mqtt_password",
    "mqtt_topic",
    "mqtt_success_message",
    "tls_ca",
    "tls_cert",
    "tls_key",
    "auth_method",
}


class ReconcileError(RuntimeError):
    """The offline monitor reconciliation cannot proceed safely."""


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def validate_schema(connection: sqlite3.Connection) -> None:
    monitor_columns = table_columns(connection, "monitor")
    missing = REQUIRED_MONITOR_COLUMNS - monitor_columns
    if missing:
        raise ReconcileError(f"unsupported Uptime Kuma monitor schema; missing={sorted(missing)}")
    if not {"id", "active", "is_default"}.issubset(table_columns(connection, "notification")):
        raise ReconcileError("unsupported Uptime Kuma notification schema")
    if not {"id", "monitor_id", "notification_id"}.issubset(
        table_columns(connection, "monitor_notification")
    ):
        raise ReconcileError("unsupported Uptime Kuma monitor-notification schema")


def assert_container_stopped(container: str, operator_confirmed: bool) -> None:
    if not operator_confirmed:
        raise ReconcileError("refusing without --container-stopped operator confirmation")
    docker = shutil.which("docker")
    if docker is None:
        return
    result = subprocess.run(
        [docker, "inspect", "--format", "{{.State.Running}}", container],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ReconcileError(f"cannot verify that container {container!r} is stopped")
    if result.stdout.strip().lower() != "false":
        raise ReconcileError(f"container {container!r} is still running")


def validate_database_path(path: Path) -> None:
    validate_no_symlink_components(path)
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ReconcileError(f"Uptime Kuma database not found: {path}") from exc
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise ReconcileError("Uptime Kuma database must be a regular, non-symlink file")


def validate_no_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ReconcileError(f"path contains a symlink component: {current}")


def load_tls_ca(path: Path | None) -> str | None:
    if path is None:
        return None
    validate_no_symlink_components(path)
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ReconcileError(f"automation TLS CA file not found: {path}") from exc
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or not (1 <= info.st_size <= 128 * 1024):
        raise ReconcileError("automation TLS CA must be a bounded regular, non-symlink file")
    try:
        value = path.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReconcileError(f"automation TLS CA is not ASCII PEM: {exc}") from exc
    if "PRIVATE KEY-----" in value:
        raise ReconcileError("automation TLS CA input contains private-key material")
    if value.count("-----BEGIN CERTIFICATE-----") < 1 or value.count(
        "-----BEGIN CERTIFICATE-----"
    ) != value.count("-----END CERTIFICATE-----"):
        raise ReconcileError("automation TLS CA input is not a complete certificate bundle")
    return value


def create_consistent_backup(
    source: sqlite3.Connection, source_path: Path, backup_root: Path
) -> Path:
    validate_no_symlink_components(backup_root)
    if backup_root.exists() and (backup_root.is_symlink() or not backup_root.is_dir()):
        raise ReconcileError(f"backup root is unsafe: {backup_root}")
    backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(backup_root, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(
        tempfile.mkdtemp(prefix=f"{stamp}-{os.getpid()}-", dir=backup_root)
    )
    os.chmod(run_dir, 0o700)
    backup_path = run_dir / source_path.name
    destination = sqlite3.connect(backup_path)
    try:
        source.backup(destination)
        result = destination.execute("PRAGMA quick_check").fetchone()
        if result != ("ok",):
            raise ReconcileError(f"private pre-change SQLite backup failed quick_check: {result!r}")
    finally:
        destination.close()
    os.chmod(backup_path, 0o600)
    return backup_path


def find_one(
    connection: sqlite3.Connection, query: str, parameters: tuple[object, ...], label: str
) -> sqlite3.Row:
    rows = connection.execute(query, parameters).fetchall()
    if len(rows) != 1:
        raise ReconcileError(f"expected exactly one {label}; found {len(rows)}")
    return rows[0]


def requested_active(state: str, existing: int | None) -> int:
    if state == "enabled":
        return 1
    if state == "disabled":
        return 0
    return 0 if existing is None else int(bool(existing))


def upsert_monitor(
    connection: sqlite3.Connection,
    spec: MonitorSpec,
    *,
    state: str,
    user_id: int,
    parent_id: int,
    tls_ca: str | None,
) -> tuple[int, int]:
    rows = connection.execute(
        "SELECT id, active, tls_ca FROM monitor WHERE name=? ORDER BY id", (spec.name,)
    ).fetchall()
    if len(rows) > 1:
        raise ReconcileError(f"duplicate monitor name requires manual review: {spec.name}")
    active = requested_active(state, rows[0][1] if rows else None)
    monitor_tls_ca = None
    auth_method = None
    if spec.monitor_type == "http":
        monitor_tls_ca = tls_ca if tls_ca is not None else (rows[0][2] if rows else None)
        # Uptime Kuma 1.x applies the custom server CA only through its mTLS
        # HTTPS agent path. Selecting that path with a CA and no client
        # certificate provides strict private-CA server verification without
        # granting the monitor a client identity.
        auth_method = "mtls"
    values = (
        active,
        user_id,
        spec.url,
        spec.monitor_type,
        spec.hostname,
        spec.port,
        spec.description,
        parent_id,
        spec.accepted_statuses,
        monitor_tls_ca,
        auth_method,
    )
    if not rows:
        cursor = connection.execute(
            """
            INSERT INTO monitor
              (name, active, user_id, interval, url, type, hostname, port,
               maxretries, ignore_tls, retry_interval, method, description,
               parent, expiry_notification, accepted_statuscodes_json,
               basic_auth_user, basic_auth_pass, mqtt_username, mqtt_password,
               mqtt_topic, mqtt_success_message, tls_ca, tls_cert, tls_key,
               auth_method)
            VALUES
              (?, ?, ?, 60, ?, ?, ?, ?, 2, 0, 60, 'GET', ?, ?, 1, ?,
               NULL, NULL, NULL, NULL, NULL, NULL, ?, NULL, NULL, ?)
            """,
            (spec.name, *values),
        )
        monitor_id = int(cursor.lastrowid)
    else:
        monitor_id = int(rows[0][0])
        connection.execute(
            """
            UPDATE monitor
            SET active=?, user_id=?, interval=60, url=?, type=?, hostname=?, port=?,
                maxretries=2, ignore_tls=0, retry_interval=60, method='GET',
                description=?, parent=?, expiry_notification=1,
                accepted_statuscodes_json=?, basic_auth_user=NULL,
                basic_auth_pass=NULL, mqtt_username=NULL, mqtt_password=NULL,
                mqtt_topic=NULL, mqtt_success_message=NULL, tls_ca=?,
                tls_cert=NULL, tls_key=NULL, auth_method=?
            WHERE id=?
            """,
            (*values, monitor_id),
        )
    return monitor_id, active


def bind_notification(
    connection: sqlite3.Connection, monitor_id: int, notification_id: int
) -> None:
    connection.execute("DELETE FROM monitor_notification WHERE monitor_id=?", (monitor_id,))
    next_id = int(
        connection.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM monitor_notification").fetchone()[0]
    )
    connection.execute(
        "INSERT INTO monitor_notification (id, monitor_id, notification_id) VALUES (?, ?, ?)",
        (next_id, monitor_id, notification_id),
    )


def reconcile(
    database_path: Path,
    backup_root: Path,
    *,
    state: str,
    parent_group: str,
    tls_ca: str | None = None,
) -> tuple[Path, list[tuple[str, int, int]]]:
    if state == "enabled" and tls_ca is None:
        raise ReconcileError("--state enabled requires --tls-ca-file for strict HTTPS verification")
    validate_database_path(database_path)
    connection = sqlite3.connect(database_path, timeout=10)
    connection.row_factory = sqlite3.Row
    backup_path: Path | None = None
    try:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ReconcileError("live Uptime Kuma database failed quick_check")
        validate_schema(connection)
        backup_path = create_consistent_backup(connection, database_path, backup_root)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        group = find_one(
            connection,
            "SELECT id, user_id FROM monitor WHERE type='group' AND name=?",
            (parent_group,),
            f"monitor group named {parent_group!r}",
        )
        notification = find_one(
            connection,
            "SELECT id FROM notification WHERE active=1 ORDER BY is_default DESC, id LIMIT 1",
            (),
            "active notification provider",
        )
        results: list[tuple[str, int, int]] = []
        for spec in MONITORS:
            monitor_id, active = upsert_monitor(
                connection,
                spec,
                state=state,
                user_id=int(group["user_id"]),
                parent_id=int(group["id"]),
                tls_ca=tls_ca,
            )
            bind_notification(connection, monitor_id, int(notification["id"]))
            results.append((spec.name, monitor_id, active))
        connection.commit()
        return backup_path, results
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--backup-root", type=Path, default=DEFAULT_BACKUP_ROOT)
    parser.add_argument("--container-name", default=DEFAULT_CONTAINER)
    parser.add_argument("--parent-group", default=DEFAULT_PARENT_GROUP)
    parser.add_argument(
        "--tls-ca-file",
        type=Path,
        default=Path(DEFAULT_TLS_CA_FILE) if DEFAULT_TLS_CA_FILE else None,
        help="Public automation CA bundle stored into the two HTTPS monitor rows.",
    )
    parser.add_argument(
        "--state",
        choices=("preserve", "enabled", "disabled"),
        default="preserve",
        help="Preserve existing states by default; newly created monitors begin disabled.",
    )
    parser.add_argument(
        "--container-stopped",
        action="store_true",
        help="Required operator assertion; a detected running container still causes refusal.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    assert_container_stopped(args.container_name, args.container_stopped)
    tls_ca = load_tls_ca(args.tls_ca_file)
    backup_path, monitors = reconcile(
        args.database,
        args.backup_root,
        state=args.state,
        parent_group=args.parent_group,
        tls_ca=tls_ca,
    )
    print(f"private_backup={backup_path}")
    for name, monitor_id, active in monitors:
        print(
            f"monitor={name!r} id={monitor_id} active={active} "
            "tls_verification=required private_credentials_stored=no"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReconcileError as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
