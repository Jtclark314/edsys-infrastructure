#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path


RUNTIME = Path("/mnt/ai-store/foothills-schedule-procurement")
DATABASE = RUNTIME / "database/schedule-procurement.sqlite3"
BACKUPS = RUNTIME / "backups"
RETENTION_DAYS = 35


def validate(path: Path) -> dict:
    uri = f"file:{path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign = connection.execute("PRAGMA foreign_key_check").fetchall()
        counts = {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in (
                "schedule_versions",
                "schedule_wbs_nodes",
                "schedule_activities",
                "tracker_imports",
                "tracker_staging_rows",
                "procurement_packages",
            )
        }
    if integrity != "ok" or foreign:
        raise RuntimeError(
            f"Backup validation failed: integrity={integrity}, foreign_keys={len(foreign)}"
        )
    return {"integrity": integrity, "foreign_key_violations": 0, "counts": counts}


def main() -> None:
    if not DATABASE.is_file():
        raise SystemExit(f"Operational database is missing: {DATABASE}")
    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = BACKUPS / f"schedule-procurement-{stamp}.sqlite3"
    source = sqlite3.connect(f"file:{DATABASE.resolve()}?mode=ro", uri=True)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
        target.execute("PRAGMA journal_mode = DELETE")
        target.commit()
    finally:
        target.close()
        source.close()
    validation = validate(destination)
    with tempfile.TemporaryDirectory(prefix="schedule-procurement-restore-") as temporary:
        restored = Path(temporary) / "restored.sqlite3"
        source = sqlite3.connect(f"file:{destination.resolve()}?mode=ro", uri=True)
        target = sqlite3.connect(restored)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        restore_validation = validate(restored)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
    pruned = []
    for candidate in BACKUPS.glob("schedule-procurement-*.sqlite3"):
        modified = datetime.fromtimestamp(candidate.stat().st_mtime, UTC)
        if modified < cutoff:
            candidate.unlink()
            pruned.append(candidate.name)
    for sidecar in BACKUPS.glob("schedule-procurement-*.sqlite3-*"):
        if sidecar.is_file():
            sidecar.unlink()
            pruned.append(sidecar.name)
    result = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "backup": str(destination),
        "sha256": digest,
        "validation": validation,
        "isolated_restore": restore_validation,
        "retention_days": RETENTION_DAYS,
        "pruned": pruned,
    }
    latest = BACKUPS / "latest-verified.json"
    temporary_latest = latest.with_suffix(".next")
    temporary_latest.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    temporary_latest.replace(latest)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
