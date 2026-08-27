from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3
import sys

import pytest


MODULE_PATH = Path(__file__).parents[1] / "configure-uptime-kuma-monitors.py"
SPEC = importlib.util.spec_from_file_location("configure_automation_kuma", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def create_database(path: Path, *, include_group: bool = True) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE monitor (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1,
          user_id INTEGER,
          interval INTEGER NOT NULL DEFAULT 20,
          url TEXT,
          type TEXT,
          hostname TEXT,
          port INTEGER,
          maxretries INTEGER NOT NULL DEFAULT 0,
          ignore_tls INTEGER NOT NULL DEFAULT 0,
          retry_interval INTEGER NOT NULL DEFAULT 0,
          method TEXT NOT NULL DEFAULT 'GET',
          description TEXT,
          parent INTEGER,
          expiry_notification INTEGER DEFAULT 1,
          accepted_statuscodes_json TEXT NOT NULL DEFAULT '["200-299"]',
          basic_auth_user TEXT,
          basic_auth_pass TEXT,
          mqtt_username TEXT,
          mqtt_password TEXT,
          mqtt_topic TEXT,
          mqtt_success_message TEXT,
          tls_ca TEXT,
          tls_cert TEXT,
          tls_key TEXT,
          auth_method TEXT
        );
        CREATE TABLE notification (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          active INTEGER NOT NULL,
          is_default INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE monitor_notification (
          id INTEGER PRIMARY KEY,
          monitor_id INTEGER NOT NULL,
          notification_id INTEGER NOT NULL
        );
        INSERT INTO notification (active, is_default) VALUES (1, 1);
        """
    )
    if include_group:
        connection.execute(
            "INSERT INTO monitor (name, active, user_id, type) VALUES (?, 1, 1, 'group')",
            (MODULE.DEFAULT_PARENT_GROUP,),
        )
    connection.commit()
    connection.close()


TLS_CA = "-----BEGIN CERTIFICATE-----\nZXhhbXBsZQ==\n-----END CERTIFICATE-----\n"


def test_reconcile_creates_enabled_private_monitors(tmp_path):
    database = tmp_path / "kuma.db"
    backups = tmp_path / "backups"
    create_database(database)

    backup_path, results = MODULE.reconcile(
        database,
        backups,
        state="enabled",
        parent_group=MODULE.DEFAULT_PARENT_GROUP,
        tls_ca=TLS_CA,
    )

    assert backup_path.is_file()
    assert backup_path.stat().st_mode & 0o077 == 0
    assert len(results) == 4
    assert {active for _name, _monitor_id, active in results} == {1}
    connection = sqlite3.connect(database)
    rows = connection.execute(
        """
        SELECT name, type, url, hostname, port, ignore_tls, parent,
               basic_auth_pass, mqtt_password, tls_ca, tls_cert, auth_method,
               accepted_statuscodes_json
        FROM monitor WHERE name LIKE 'EdCore Automation %' ORDER BY name
        """
    ).fetchall()
    assert len(rows) == 4
    assert all(row[5] == 0 for row in rows)
    assert all(row[6] == 1 for row in rows)
    assert all(row[7] is None and row[8] is None and row[10] is None for row in rows)
    assert all(row[9] is None for row in rows if row[1] != "http")
    assert all(row[9] == TLS_CA for row in rows if row[1] == "http")
    assert all(row[11] is None for row in rows if row[1] != "http")
    assert all(row[11] == "mtls" for row in rows if row[1] == "http")
    assert connection.execute(
        "SELECT COUNT(*) FROM monitor WHERE type='http' AND tls_ca=?", (TLS_CA,)
    ).fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM monitor_notification").fetchone()[0] == 4
    connection.close()


def test_reconcile_is_idempotent_and_preserves_state(tmp_path):
    database = tmp_path / "kuma.db"
    backups = tmp_path / "backups"
    create_database(database)
    MODULE.reconcile(
        database,
        backups,
        state="enabled",
        parent_group=MODULE.DEFAULT_PARENT_GROUP,
        tls_ca=TLS_CA,
    )
    MODULE.reconcile(database, backups, state="preserve", parent_group=MODULE.DEFAULT_PARENT_GROUP)
    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT COUNT(*) FROM monitor WHERE name LIKE 'EdCore Automation %'"
    ).fetchone()[0] == 4
    assert connection.execute(
        "SELECT COUNT(*) FROM monitor WHERE name LIKE 'EdCore Automation %' AND active=1"
    ).fetchone()[0] == 4
    assert connection.execute(
        "SELECT COUNT(*) FROM monitor WHERE type='http' AND name LIKE 'EdCore Automation %' AND tls_ca=?",
        (TLS_CA,),
    ).fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM monitor_notification").fetchone()[0] == 4
    connection.close()
    assert len(list(backups.glob("*/kuma.db"))) == 2


def test_new_monitors_are_disabled_in_preserve_mode(tmp_path):
    database = tmp_path / "kuma.db"
    create_database(database)
    _backup, results = MODULE.reconcile(
        database,
        tmp_path / "backups",
        state="preserve",
        parent_group=MODULE.DEFAULT_PARENT_GROUP,
    )
    assert {active for _name, _monitor_id, active in results} == {0}


def test_missing_parent_group_rolls_back(tmp_path):
    database = tmp_path / "kuma.db"
    create_database(database, include_group=False)
    with pytest.raises(MODULE.ReconcileError, match="expected exactly one monitor group"):
        MODULE.reconcile(
            database,
            tmp_path / "backups",
            state="enabled",
            parent_group=MODULE.DEFAULT_PARENT_GROUP,
            tls_ca=TLS_CA,
        )
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT COUNT(*) FROM monitor").fetchone()[0] == 0
    connection.close()


def test_requires_explicit_stopped_confirmation():
    with pytest.raises(MODULE.ReconcileError, match="--container-stopped"):
        MODULE.assert_container_stopped("uptime-kuma", False)


def test_enabled_state_requires_ca(tmp_path):
    database = tmp_path / "kuma.db"
    create_database(database)
    with pytest.raises(MODULE.ReconcileError, match="requires --tls-ca-file"):
        MODULE.reconcile(
            database,
            tmp_path / "backups",
            state="enabled",
            parent_group=MODULE.DEFAULT_PARENT_GROUP,
        )


def test_tls_ca_loader_accepts_public_certificate_bundle(tmp_path):
    ca_file = tmp_path / "ca.crt"
    ca_file.write_text(TLS_CA, encoding="ascii")
    assert MODULE.load_tls_ca(ca_file) == TLS_CA


def test_tls_ca_loader_rejects_private_key_material(tmp_path):
    ca_file = tmp_path / "not-a-ca.pem"
    private_key_marker = "-----BEGIN " + "TEST PRIVATE KEY-----\n"
    private_key_end = "-----END " + "TEST PRIVATE KEY-----\n"
    ca_file.write_text(
        TLS_CA + private_key_marker + "example\n" + private_key_end,
        encoding="ascii",
    )
    with pytest.raises(MODULE.ReconcileError, match="private-key material"):
        MODULE.load_tls_ca(ca_file)
