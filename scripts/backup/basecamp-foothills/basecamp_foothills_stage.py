#!/usr/bin/env python3
"""Create a consistent, private Basecamp recovery stage.

The stage is intentionally file-oriented so the 9950x restic repository can
deduplicate unchanged application files. Live SQLite databases are copied
through SQLite's online backup API rather than copied as ordinary files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import stat
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA_VERSION = 1
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
IGNORED_NAMES = {
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
}
IGNORED_DIRS = {
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "Cache",
    "cache",
    "logs",
    "migration-archive",
    "migration-staging",
    "node_modules",
    "restore-tests",
    "temp",
    "tmp",
}
IGNORED_SUFFIXES = {
    ".log",
    ".pid",
    ".pyc",
    ".shm",
    ".sock",
    ".tmp",
    ".wal",
}


@dataclass(frozen=True)
class CopySource:
    source: Path
    destination: PurePosixPath
    category: str
    required: bool = True
    recursive: bool = True


@dataclass(frozen=True)
class DatabaseSource:
    source: Path
    destination: PurePosixPath
    category: str
    required: bool = True


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    return bool(attributes & REPARSE_POINT) or path.is_symlink()


def should_ignore(path: Path) -> bool:
    if path.name in IGNORED_NAMES:
        return True
    if path.is_dir() and path.name in IGNORED_DIRS:
        return True
    return path.is_file() and path.suffix.lower() in IGNORED_SUFFIXES


def ensure_safe_relative(path: PurePosixPath) -> None:
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"Unsafe destination path: {path}")


def copy_file_stable(source: Path, destination: Path, retries: int = 4) -> None:
    """Copy a file only when size and mtime stay stable across the operation."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for attempt in range(retries):
        # Keep the temporary name short. Prefixing the full destination filename
        # pushed otherwise valid backup paths over Windows MAX_PATH.
        name_hash = hashlib.sha256(destination.name.encode("utf-8")).hexdigest()[:12]
        partial = destination.with_name(f".partial-{os.getpid()}-{name_hash}")
        try:
            before = source.stat()
            shutil.copy2(source, partial)
            after = source.stat()
            if (
                before.st_size == after.st_size
                and before.st_mtime_ns == after.st_mtime_ns
                and partial.stat().st_size == after.st_size
            ):
                os.replace(partial, destination)
                return
        except OSError as error:
            last_error = error
        finally:
            partial.unlink(missing_ok=True)
        time.sleep(0.25 * (attempt + 1))
    detail = f": {last_error}" if last_error else ""
    raise RuntimeError(
        f"Source could not be copied consistently: {source} -> {destination}{detail}"
    )


def copy_tree_stable(source: Path, destination: Path) -> None:
    if is_reparse_point(source):
        raise RuntimeError(f"Refusing reparse-point source: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    for child in sorted(source.iterdir(), key=lambda item: item.name.lower()):
        if should_ignore(child):
            continue
        if is_reparse_point(child):
            raise RuntimeError(f"Refusing reparse point inside backup source: {child}")
        target = destination / child.name
        if child.is_dir():
            copy_tree_stable(child, target)
        elif child.is_file():
            copy_file_stable(child, target)


def copy_source(item: CopySource, stage: Path, missing: list[str]) -> None:
    ensure_safe_relative(item.destination)
    if not item.source.exists():
        if item.required:
            raise FileNotFoundError(f"Required backup source is missing: {item.source}")
        missing.append(str(item.source))
        return
    destination = stage.joinpath(*item.destination.parts)
    if item.source.is_dir():
        if not item.recursive:
            raise ValueError(f"Directory source must be recursive: {item.source}")
        copy_tree_stable(item.source, destination)
    else:
        copy_file_stable(item.source, destination)


def sqlite_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


def validate_sqlite(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(sqlite_uri(path), uri=True, timeout=30)
    try:
        connection.execute("PRAGMA query_only=ON")
        integrity_rows = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
        table_count = connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
    finally:
        connection.close()
    if integrity_rows != ["ok"]:
        raise RuntimeError(f"SQLite integrity check failed for {path}: {integrity_rows}")
    if foreign_keys:
        raise RuntimeError(
            f"SQLite foreign-key check failed for {path}: {len(foreign_keys)} rows"
        )
    return {
        "path": path.as_posix(),
        "integrity": "ok",
        "foreign_key_violations": 0,
        "table_count": table_count,
    }


def backup_sqlite(item: DatabaseSource, stage: Path, missing: list[str]) -> dict[str, Any] | None:
    ensure_safe_relative(item.destination)
    if not item.source.exists():
        if item.required:
            raise FileNotFoundError(f"Required SQLite source is missing: {item.source}")
        missing.append(str(item.source))
        return None
    destination = stage.joinpath(*item.destination.parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.partial-{os.getpid()}")
    partial.unlink(missing_ok=True)
    source_connection = sqlite3.connect(str(item.source), timeout=60)
    destination_connection = sqlite3.connect(str(partial), timeout=60)
    try:
        source_connection.execute("PRAGMA query_only=ON")
        source_connection.backup(destination_connection, pages=1024, sleep=0.05)
        destination_connection.commit()
    finally:
        destination_connection.close()
        source_connection.close()
    os.replace(partial, destination)
    result = validate_sqlite(destination)
    result["source"] = str(item.source)
    result["relative_path"] = item.destination.as_posix()
    return result


def validate_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as stream:
        value = json.load(stream)
    return {
        "path": path.as_posix(),
        "type": type(value).__name__,
        "status": "ok",
    }


def validate_zip(path: Path, required_database: str | None = None) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        for name in names:
            normalized = name.replace("\\", "/")
            candidate = PurePosixPath(normalized)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise RuntimeError(f"Unsafe path in {path.name}: {name}")
        failed = archive.testzip()
        if failed:
            raise RuntimeError(f"CRC failure in {path.name}: {failed}")
        database_names = [
            name for name in names if name.lower().endswith((".db", ".db3", ".sqlite", ".sqlite3"))
        ]
        if required_database and required_database not in names:
            raise RuntimeError(f"{path.name} lacks required database {required_database}")
        database_name = required_database or (database_names[0] if database_names else None)
        database_validation = None
        if database_name:
            with tempfile.TemporaryDirectory(prefix="basecamp-zip-db-") as temporary:
                database = Path(temporary) / PurePosixPath(database_name).name
                database.write_bytes(archive.read(database_name))
                database_validation = validate_sqlite(database)
    return {
        "path": path.as_posix(),
        "entry_count": len(names),
        "crc": "ok",
        "unsafe_paths": 0,
        "database": database_name,
        "database_validation": database_validation,
    }


def latest_file(directory: Path, pattern: str) -> Path:
    files = sorted(
        directory.glob(pattern),
        key=lambda item: (item.stat().st_mtime_ns, item.name),
        reverse=True,
    )
    if not files:
        raise FileNotFoundError(f"No file matches {directory / pattern}")
    return files[0]


def copy_verified_archive(
    source: Path,
    destination: Path,
    relative_path: PurePosixPath,
    required_database: str | None,
) -> dict[str, Any]:
    ensure_safe_relative(relative_path)
    sidecar = source.with_name(source.name + ".sha256")
    expected = None
    if sidecar.exists():
        expected = sidecar.read_text(encoding="ascii").split()[0].lower()
        if len(expected) != 64:
            raise RuntimeError(f"Invalid SHA-256 sidecar: {sidecar}")
    actual = sha256_file(source)
    if expected and actual != expected:
        raise RuntimeError(f"Archive hash mismatch: {source}")
    copy_file_stable(source, destination)
    copied = sha256_file(destination)
    if copied != actual:
        raise RuntimeError(f"Copied archive hash mismatch: {source}")
    validation = validate_zip(destination, required_database=required_database)
    validation.update(
        {
            "source": str(source),
            "relative_path": relative_path.as_posix(),
            "sha256": copied,
            "size": destination.stat().st_size,
        }
    )
    return validation


def default_copy_sources(recovery_source: Path | None) -> list[CopySource]:
    sources = [
        CopySource(
            Path(r"C:\Foothills\TaskList\data\uploads"),
            PurePosixPath("apps/task-list/data/uploads"),
            "task-list",
        ),
        CopySource(
            Path(r"C:\Foothills\TaskList\scripts"),
            PurePosixPath("apps/task-list/recovery/scripts"),
            "task-list",
        ),
        CopySource(
            Path(r"C:\Foothills\ASITracker\data\uploads"),
            PurePosixPath("apps/asi-tracker/data/uploads"),
            "asi-tracker",
        ),
        CopySource(
            Path(r"C:\Foothills\ASITracker\data\backups"),
            PurePosixPath("apps/asi-tracker/legacy-backups"),
            "asi-tracker",
            required=False,
        ),
        CopySource(
            Path(r"C:\Foothills\ASITracker\scripts"),
            PurePosixPath("apps/asi-tracker/recovery/scripts"),
            "asi-tracker",
        ),
        # Observation Tracker V3 is the active production runtime. Its database
        # is captured separately through SQLite's online backup API below.
        CopySource(
            Path(r"C:\Foothills\ObservationTrackerV3\runtime\evidence"),
            PurePosixPath("apps/observation-tracker/data/evidence"),
            "observation-tracker-v3",
        ),
        CopySource(
            Path(r"C:\Foothills\ObservationTrackerV3\runtime\derivatives"),
            PurePosixPath("apps/observation-tracker/data/derivatives"),
            "observation-tracker-v3",
        ),
        CopySource(
            Path(r"C:\Foothills\ObservationTrackerV3\runtime\reports"),
            PurePosixPath("apps/observation-tracker/data/reports"),
            "observation-tracker-v3",
        ),
        CopySource(
            Path(r"C:\Foothills\ObservationTrackerV3\runtime\backups"),
            PurePosixPath("apps/observation-tracker/verified-backups"),
            "observation-tracker-v3",
        ),
        CopySource(
            Path(r"C:\Foothills\ObservationTrackerV3\config"),
            PurePosixPath("apps/observation-tracker/recovery/config"),
            "observation-tracker-v3",
        ),
        CopySource(
            Path(r"C:\Foothills\ObservationTrackerV3\operations"),
            PurePosixPath("apps/observation-tracker/recovery/operations"),
            "observation-tracker-v3",
        ),
        # Preserve the disabled V2 runtime as an explicit rollback set until it
        # is retired through a separate reviewed operation.
        CopySource(
            Path(r"C:\Foothills\ObservationTracker\data\uploads"),
            PurePosixPath("apps/observation-tracker/legacy-v2/data/uploads"),
            "observation-tracker-v2-rollback",
        ),
        CopySource(
            Path(r"C:\Foothills\ObservationTracker\backups"),
            PurePosixPath("apps/observation-tracker/legacy-v2/backups"),
            "observation-tracker-v2-rollback",
        ),
        CopySource(
            Path(r"C:\Foothills\ObservationTracker\scripts"),
            PurePosixPath("apps/observation-tracker/legacy-v2/scripts"),
            "observation-tracker-v2-rollback",
        ),
        CopySource(
            Path(r"C:\Foothills\UnitSelections\intake"),
            PurePosixPath("apps/unit-selections/intake"),
            "unit-selections",
        ),
        CopySource(
            Path(r"C:\Foothills\UnitSelections\config"),
            PurePosixPath("apps/unit-selections/config"),
            "unit-selections",
        ),
        CopySource(
            Path(r"C:\Foothills\UnitSelections\operations"),
            PurePosixPath("apps/unit-selections/operations"),
            "unit-selections",
        ),
        CopySource(
            Path(r"C:\EdSys\Foothills-Project-Portal\current-site"),
            PurePosixPath("apps/project-portal/current-site"),
            "project-portal",
        ),
        CopySource(
            Path(r"C:\EdSys\Foothills-Project-Portal\operations\backups"),
            PurePosixPath("apps/project-portal/operations/backups"),
            "project-portal",
        ),
        CopySource(
            Path(r"C:\EdSys\Foothills-Project-Portal\operations\cameras"),
            PurePosixPath("apps/project-portal/operations/cameras"),
            "project-portal",
            required=False,
        ),
        CopySource(
            Path(r"C:\Foothills\Cameras\exports"),
            PurePosixPath("services/cameras/exports"),
            "cameras",
            required=False,
        ),
        CopySource(
            Path(r"C:\Program Files\Agent\Media\XML"),
            PurePosixPath("services/agent-dvr/xml"),
            "agent-dvr",
        ),
        CopySource(
            Path(r"C:\EdSys\Speakr\uploads"),
            PurePosixPath("apps/speakr/uploads"),
            "speakr",
        ),
        CopySource(
            Path(r"C:\EdSys\Speakr\exports"),
            PurePosixPath("apps/speakr/exports"),
            "speakr",
        ),
        CopySource(
            Path(r"C:\EdSys\Speakr\backups"),
            PurePosixPath("apps/speakr/legacy-backups"),
            "speakr",
        ),
        CopySource(
            Path(r"C:\EdSys\Speakr\tls"),
            PurePosixPath("apps/speakr/tls"),
            "speakr",
            required=False,
        ),
        CopySource(
            Path(r"C:\EdSys\Speakr\.env"),
            PurePosixPath("apps/speakr/config/.env"),
            "speakr",
        ),
        CopySource(
            Path(r"C:\EdSys\SpeakrNative\config"),
            PurePosixPath("apps/speakr/native-config"),
            "speakr",
        ),
        CopySource(
            Path(r"C:\EdSys\SpeakrNative\operations"),
            PurePosixPath("apps/speakr/native-operations"),
            "speakr",
        ),
        CopySource(
            Path(r"C:\EdSys\KindleDrop\share\30-Returned-Annotated"),
            PurePosixPath("apps/kindle-drop/returned-annotated"),
            "kindle-drop",
        ),
        CopySource(
            Path(r"C:\EdSys\KindleDrop\share\90-Receipts"),
            PurePosixPath("apps/kindle-drop/receipts"),
            "kindle-drop",
        ),
        CopySource(
            Path(r"C:\EdSys\KindleDrop\settings.json"),
            PurePosixPath("apps/kindle-drop/config/settings.json"),
            "kindle-drop",
        ),
        CopySource(
            Path(r"C:\EdSys\KindleDrop\current.txt"),
            PurePosixPath("apps/kindle-drop/config/current.txt"),
            "kindle-drop",
        ),
        CopySource(
            Path(r"C:\EdSys\KindleDrop\operations"),
            PurePosixPath("apps/kindle-drop/operations"),
            "kindle-drop",
        ),
        CopySource(
            Path(r"C:\ProgramData\ssh"),
            PurePosixPath("host/openssh"),
            "host-recovery",
        ),
    ]
    if recovery_source:
        sources.append(
            CopySource(
                recovery_source,
                PurePosixPath("host/recovery-exports"),
                "host-recovery",
            )
        )
    return sources


def default_database_sources() -> list[DatabaseSource]:
    return [
        DatabaseSource(
            Path(r"C:\Foothills\TaskList\data\tasks.sqlite3"),
            PurePosixPath("apps/task-list/data/tasks.sqlite3"),
            "task-list",
        ),
        DatabaseSource(
            Path(r"C:\Foothills\ASITracker\data\asi_tracker.sqlite3"),
            PurePosixPath("apps/asi-tracker/data/asi_tracker.sqlite3"),
            "asi-tracker",
        ),
        DatabaseSource(
            Path(
                r"C:\Foothills\ObservationTrackerV3\runtime\database\observation_tracker.sqlite3"
            ),
            PurePosixPath("apps/observation-tracker/data/observation_tracker.sqlite3"),
            "observation-tracker-v3",
        ),
        DatabaseSource(
            Path(r"C:\Foothills\ObservationTracker\data\observation_tracker.sqlite3"),
            PurePosixPath(
                "apps/observation-tracker/legacy-v2/data/observation_tracker.sqlite3"
            ),
            "observation-tracker-v2-rollback",
        ),
        DatabaseSource(
            Path(r"C:\EdSys\Speakr\instance\transcriptions.db"),
            PurePosixPath("apps/speakr/instance/transcriptions.db"),
            "speakr",
        ),
        DatabaseSource(
            Path(r"C:\Program Files\Agent\Media\XML\fileDB.db3"),
            PurePosixPath("services/agent-dvr/xml/fileDB-consistent.db3"),
            "agent-dvr",
            required=False,
        ),
    ]


def collect_file_manifest(stage: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(stage.rglob("*"), key=lambda item: item.as_posix().lower()):
        if path.is_dir():
            continue
        if is_reparse_point(path):
            raise RuntimeError(f"Reparse point created inside stage: {path}")
        relative = path.relative_to(stage).as_posix()
        if relative == "manifest.json":
            continue
        entries.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return entries


def activate_stage(output_root: Path, candidate: Path) -> None:
    current = output_root / "current"
    previous = output_root / "previous"
    old = output_root / f".old-{os.getpid()}"
    if old.exists():
        shutil.rmtree(old)
    if previous.exists():
        os.replace(previous, old)
    if current.exists():
        os.replace(current, previous)
    os.replace(candidate, current)
    if old.exists():
        shutil.rmtree(old)


def create_stage(output_root: Path, recovery_source: Path | None) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    generation = utc_now().strftime("%Y%m%dT%H%M%SZ")
    candidate = output_root / f".candidate-{generation}-{os.getpid()}"
    if candidate.exists():
        shutil.rmtree(candidate)
    candidate.mkdir(parents=True)
    missing: list[str] = []
    validations: dict[str, Any] = {
        "sqlite": [],
        "archives": [],
        "json": [],
    }
    try:
        for source in default_copy_sources(recovery_source):
            copy_source(source, candidate, missing)
        for database in default_database_sources():
            result = backup_sqlite(database, candidate, missing)
            if result:
                validations["sqlite"].append(result)

        unit_archive = latest_file(
            Path(r"C:\Foothills\UnitSelections\data\backups"),
            "unit-selections-daily-*.zip",
        )
        validations["archives"].append(
            copy_verified_archive(
                unit_archive,
                candidate / "apps/unit-selections/backups" / unit_archive.name,
                PurePosixPath("apps/unit-selections/backups") / unit_archive.name,
                "unit-selections/unit_selections.sqlite3",
            )
        )
        unit_monthly = latest_file(
            Path(r"C:\Foothills\UnitSelections\data\backups"),
            "unit-selections-monthly-*.zip",
        )
        validations["archives"].append(
            copy_verified_archive(
                unit_monthly,
                candidate / "apps/unit-selections/backups" / unit_monthly.name,
                PurePosixPath("apps/unit-selections/backups") / unit_monthly.name,
                "unit-selections/unit_selections.sqlite3",
            )
        )

        kindle_archive = latest_file(
            Path(r"C:\EdSys\KindleDrop\backups"),
            "kindle-drop-daily-*.zip",
        )
        validations["archives"].append(
            copy_verified_archive(
                kindle_archive,
                candidate / "apps/kindle-drop/backups" / kindle_archive.name,
                PurePosixPath("apps/kindle-drop/backups") / kindle_archive.name,
                "state/kindle-drop.sqlite3",
            )
        )
        kindle_monthly = latest_file(
            Path(r"C:\EdSys\KindleDrop\backups"),
            "kindle-drop-monthly-*.zip",
        )
        validations["archives"].append(
            copy_verified_archive(
                kindle_monthly,
                candidate / "apps/kindle-drop/backups" / kindle_monthly.name,
                PurePosixPath("apps/kindle-drop/backups") / kindle_monthly.name,
                "state/kindle-drop.sqlite3",
            )
        )

        portal_json = candidate / "apps/project-portal/current-site/data/portal-content.json"
        validations["json"].append(validate_json(portal_json))

        files = collect_file_manifest(candidate)
        category_counts: dict[str, int] = {}
        category_bytes: dict[str, int] = {}
        for entry in files:
            category = entry["path"].split("/", 2)[:2]
            category_name = "/".join(category)
            category_counts[category_name] = category_counts.get(category_name, 0) + 1
            category_bytes[category_name] = category_bytes.get(category_name, 0) + entry["size"]

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "generation": generation,
            "created_at": utc_now().isoformat(),
            "host": socket.gethostname(),
            "status": "verified",
            "file_count": len(files),
            "total_bytes": sum(entry["size"] for entry in files),
            "category_counts": category_counts,
            "category_bytes": category_bytes,
            "missing_optional_sources": missing,
            "files": files,
            "validations": validations,
        }
        manifest_path = candidate / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest["manifest_sha256"] = sha256_file(manifest_path)
        activate_stage(output_root, candidate)
        return manifest
    except Exception:
        if candidate.exists():
            shutil.rmtree(candidate)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(r"C:\Foothills\OffsiteBackup"),
    )
    parser.add_argument("--recovery-source", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    manifest = create_stage(arguments.output_root, arguments.recovery_source)
    print(
        json.dumps(
            {
                "status": "success",
                "generation": manifest["generation"],
                "file_count": manifest["file_count"],
                "total_bytes": manifest["total_bytes"],
                "manifest_sha256": manifest["manifest_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
