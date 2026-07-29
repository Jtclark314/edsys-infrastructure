#!/usr/bin/env python3
"""Create SQLite-consistent recovery copies of the Foothills local catalogs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def backup_database(source: Path, destination: Path) -> dict[str, object]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(str(source), timeout=60)
    destination_connection = sqlite3.connect(str(destination), timeout=60)
    try:
        source_connection.execute("PRAGMA query_only=ON")
        source_connection.backup(destination_connection, pages=1024, sleep=0.05)
        destination_connection.commit()
    finally:
        destination_connection.close()
        source_connection.close()
    check = sqlite3.connect(f"{destination.as_uri()}?mode=ro", uri=True)
    try:
        integrity = [row[0] for row in check.execute("PRAGMA integrity_check")]
        foreign_keys = list(check.execute("PRAGMA foreign_key_check"))
    finally:
        check.close()
    if integrity != ["ok"] or foreign_keys:
        raise RuntimeError(f"Catalog validation failed: {source}")
    return {
        "source": str(source),
        "file": destination.name,
        "size": destination.stat().st_size,
        "sha256": digest(destination),
        "integrity": "ok",
        "foreign_key_violations": 0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path("/home/jeremy/projects/foothills"),
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path("/srv/edsys-backup/staging/foothills-project"),
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    controls = arguments.project_root / "project-management/project-controls"
    sources = [
        controls / "foothills-catalog.sqlite3",
        controls / "foothills-query-index.sqlite3",
    ]
    for source in sources:
        if not source.is_file():
            raise FileNotFoundError(source)
    candidate = arguments.destination / f".candidate-{os.getpid()}"
    if candidate.exists():
        shutil.rmtree(candidate)
    candidate.mkdir(parents=True)
    try:
        results = [backup_database(source, candidate / source.name) for source in sources]
        manifest = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "project_root": str(arguments.project_root),
            "databases": results,
        }
        (candidate / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        current = arguments.destination / "current"
        previous = arguments.destination / "previous"
        stale = arguments.destination / f".stale-{os.getpid()}"
        if stale.exists():
            shutil.rmtree(stale)
        if previous.exists():
            os.replace(previous, stale)
        if current.exists():
            os.replace(current, previous)
        os.replace(candidate, current)
        if stale.exists():
            shutil.rmtree(stale)
    except Exception:
        if candidate.exists():
            shutil.rmtree(candidate)
        raise
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
