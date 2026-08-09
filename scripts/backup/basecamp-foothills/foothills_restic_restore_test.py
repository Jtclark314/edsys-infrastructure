#!/usr/bin/env python3
"""Restore and validate representative Foothills recovery artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


PROTECTED_FILES = (
    "/mnt/ai-store/foothills-basecamp-offsite/current/manifest.json",
    "/mnt/ai-store/foothills-basecamp-offsite/current/apps/task-list/data/tasks.sqlite3",
    "/mnt/ai-store/foothills-basecamp-offsite/current/apps/observation-tracker/data/observation_tracker.sqlite3",
    "/mnt/ai-store/foothills-basecamp-offsite/current/apps/project-portal/current-site/data/portal-content.json",
    "/mnt/ai-store/foothills-project/00-FOOTHILLS-WORKING-MEMORY.md",
    "/srv/edsys-backup/staging/foothills-project/current/manifest.json",
    "/srv/edsys-backup/staging/foothills-project/current/foothills-catalog.sqlite3",
    "/srv/edsys-backup/staging/foothills-project/current/foothills-query-index.sqlite3",
)


def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, text=True, **kwargs)


def sqlite_check(path: Path) -> dict[str, object]:
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
        tables = connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
    finally:
        connection.close()
    if integrity != ["ok"] or foreign_keys:
        raise RuntimeError(f"Restored SQLite validation failed: {path}")
    return {
        "path": str(path),
        "integrity": "ok",
        "foreign_key_violations": 0,
        "table_count": tables,
        "sha256": sha256(path),
        "size": path.stat().st_size,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path("/srv/edsys-backup/restic-repo/edsys-critical"),
    )
    parser.add_argument(
        "--password-file",
        type=Path,
        default=Path("/etc/edsys-backup/restic-password"),
    )
    parser.add_argument(
        "--status-file",
        type=Path,
        default=Path("/var/lib/edsys-backup/foothills-restore-test-status.json"),
    )
    parser.add_argument(
        "--work-root",
        type=Path,
        default=Path("/srv/edsys-backup/restore-tests/foothills"),
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    snapshots = json.loads(
        run(
            [
                "restic",
                "--no-cache",
                "--no-lock",
                "--repo",
                str(arguments.repository),
                "--password-file",
                str(arguments.password_file),
                "snapshots",
                "--json",
            ],
            capture_output=True,
        ).stdout
    )
    eligible = [
        item
        for item in snapshots
        if "/mnt/ai-store/foothills-basecamp-offsite" in item.get("paths", [])
        and "/mnt/ai-store/foothills-project" in item.get("paths", [])
    ]
    if not eligible:
        raise RuntimeError("No Restic snapshot contains both Foothills recovery roots")
    snapshot = max(eligible, key=lambda item: item["time"])
    snapshot_id = snapshot["id"]

    arguments.work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f"{snapshot_id[:8]}-", dir=arguments.work_root
    ) as temporary:
        target = Path(temporary)
        command = [
            "restic",
            "--no-cache",
            "--no-lock",
            "--repo",
            str(arguments.repository),
            "--password-file",
            str(arguments.password_file),
            "restore",
            snapshot_id,
            "--target",
            str(target),
        ]
        for protected in PROTECTED_FILES:
            command.extend(["--include", protected])
        run(command, capture_output=True)

        restored = {path: target / path.lstrip("/") for path in PROTECTED_FILES}
        missing = [path for path, candidate in restored.items() if not candidate.is_file()]
        if missing:
            raise RuntimeError(f"Representative restore is missing: {missing}")

        basecamp_manifest = json.loads(
            restored[PROTECTED_FILES[0]].read_text(encoding="utf-8-sig")
        )
        manifest_entries = {
            item["path"]: item for item in basecamp_manifest.get("files", [])
        }
        task_relative = "apps/task-list/data/tasks.sqlite3"
        task_database = restored[PROTECTED_FILES[1]]
        task_entry = manifest_entries.get(task_relative)
        if (
            task_entry is None
            or int(task_entry["size"]) != task_database.stat().st_size
            or task_entry["sha256"] != sha256(task_database)
        ):
            raise RuntimeError("Restored Task List database does not match its manifest")

        observation_relative = (
            "apps/observation-tracker/data/observation_tracker.sqlite3"
        )
        observation_database = restored[PROTECTED_FILES[2]]
        observation_entry = manifest_entries.get(observation_relative)
        if (
            observation_entry is None
            or int(observation_entry["size"]) != observation_database.stat().st_size
            or observation_entry["sha256"] != sha256(observation_database)
        ):
            raise RuntimeError(
                "Restored Observation Tracker database does not match its manifest"
            )

        portal = json.loads(restored[PROTECTED_FILES[3]].read_text(encoding="utf-8-sig"))
        if not isinstance(portal, (dict, list)):
            raise RuntimeError("Restored portal content has an unsupported JSON type")

        catalog_manifest = json.loads(
            restored[PROTECTED_FILES[5]].read_text(encoding="utf-8-sig")
        )
        catalog_entries = {item["file"]: item for item in catalog_manifest["databases"]}
        database_results = []
        for protected in PROTECTED_FILES[6:]:
            database = restored[protected]
            result = sqlite_check(database)
            expected = catalog_entries.get(database.name)
            if (
                expected is None
                or expected["sha256"] != result["sha256"]
                or int(expected["size"]) != result["size"]
            ):
                raise RuntimeError(
                    f"Restored project catalog does not match its manifest: {database.name}"
                )
            database_results.append(result)

        report = {
            "status": "success",
            "tested_at": datetime.now(timezone.utc).isoformat(),
            "snapshot_id": snapshot_id,
            "snapshot_time": snapshot["time"],
            "restored_file_count": len(restored),
            "basecamp_generation": basecamp_manifest["generation"],
            "task_database": sqlite_check(task_database),
            "observation_database": sqlite_check(observation_database),
            "project_databases": database_results,
            "portal_json": "ok",
            "working_memory_sha256": sha256(restored[PROTECTED_FILES[4]]),
            "temporary_restore_removed": True,
        }
    arguments.status_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_status = arguments.status_file.with_name(
        f".{arguments.status_file.name}.{os.getpid()}"
    )
    temporary_status.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    os.replace(temporary_status, arguments.status_file)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
