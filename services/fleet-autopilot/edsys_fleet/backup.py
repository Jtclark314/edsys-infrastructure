from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import FleetConfig
from .store import SCHEMA_VERSION, FleetStore


class BackupError(RuntimeError):
    pass


def encrypted_backup_and_restore_test(config: FleetConfig) -> dict[str, Any]:
    store = FleetStore(config.state_root)
    root = config.private_artifact_root / "backups"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = Path(
        os.getenv(
            "EDSYS_FLEET_BACKUP_AGE_KEY",
            str(Path.home() / ".local/share/edsys-fleet-autopilot/backup-age-key.txt"),
        )
    )
    if not key.is_file() or key.stat().st_mode & 0o077:
        raise BackupError("Fleet backup age identity is missing or not private")
    recipient = subprocess.run(
        ["age-keygen", "-y", str(key)], text=True, capture_output=True, timeout=20, check=False
    )
    if recipient.returncode != 0 or not recipient.stdout.strip().startswith("age1"):
        raise BackupError("Could not derive Fleet backup age recipient")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    encrypted = root / f"fleet-control-{stamp}.sqlite.age"
    with tempfile.TemporaryDirectory(prefix="edsys-fleet-backup-") as directory:
        plain = Path(directory) / "fleet-control.sqlite"
        restored = Path(directory) / "restored.sqlite"
        source = store.backup(plain)
        encrypt = subprocess.run(
            ["age", "-r", recipient.stdout.strip(), "-o", str(encrypted), str(plain)],
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
        if encrypt.returncode != 0:
            raise BackupError("Fleet database encryption failed")
        os.chmod(encrypted, 0o600)
        decrypt = subprocess.run(
            ["age", "-d", "-i", str(key), "-o", str(restored), str(encrypted)],
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
        if decrypt.returncode != 0:
            raise BackupError("Fleet database isolated decrypt/restore failed")
        restored_db = sqlite3.connect(f"file:{restored}?mode=ro", uri=True)
        try:
            quick = str(restored_db.execute("PRAGMA quick_check").fetchone()[0])
            schema = int(restored_db.execute("PRAGMA user_version").fetchone()[0])
            table_count = int(
                restored_db.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchone()[0]
            )
        finally:
            restored_db.close()
        if quick != "ok" or schema != SCHEMA_VERSION or table_count < 10:
            raise BackupError("Fleet database isolated restore validation failed")
    digest = hashlib.sha256(encrypted.read_bytes()).hexdigest()
    backups = sorted(root.glob("fleet-control-*.sqlite.age"), key=lambda item: item.stat().st_mtime, reverse=True)
    for obsolete in backups[35:]:
        obsolete.unlink(missing_ok=True)
    detailed_cutoff = datetime.now(timezone.utc).timestamp() - (config.event_retention_days * 86400)
    detailed_cutoff_iso = datetime.fromtimestamp(detailed_cutoff, timezone.utc).isoformat()
    pruned = store.prune_detailed_history(detailed_cutoff_iso)
    result = {
        "status": "passed",
        "encrypted_backup": str(encrypted),
        "sha256": digest,
        "bytes": encrypted.stat().st_size,
        "source_sha256": source["sha256"],
        "quick_check": quick,
        "schema_version": schema,
        "table_count": table_count,
        "plaintext_retained": False,
        "detailed_history_retention_days": config.event_retention_days,
        "pruned_after_verified_backup": pruned,
    }
    store.record_acceptance_gate(
        name="database_restore",
        status="passed",
        verified_by="edsys-fleet-backup",
        evidence={
            "schema_version": schema,
            "quick_check": quick,
            "encrypted_sha256": digest,
            "plaintext_retained": False,
        },
    )
    return result
