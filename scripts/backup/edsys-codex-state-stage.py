#!/usr/bin/env python3
"""Create and verify private, SQLite-consistent Codex state snapshots.

This stages only the mutable SQLite databases and small state indexes. The
normal encrypted EdSys backup continues to protect the remaining Codex home.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import tomllib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


DEFAULT_CODEX_HOME = Path("/home/jeremy/.codex")
DEFAULT_STAGE_ROOT = Path("/srv/edsys-backup/staging/codex-state")
DEFAULT_LOCK_FILE = Path("/run/edsys-codex-state-stage.lock")
MAX_INDEX_BYTES = 64 * 1024 * 1024
INDEX_FILES = (
    "AGENTS.md",
    "config.toml",
    "history.jsonl",
    "installation_id",
    "session_index.jsonl",
    "version.json",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_mode(path: Path, mode: int) -> None:
    path.chmod(mode)


def fsync_path(path: Path) -> None:
    """Flush a regular file or directory entry before atomic publication."""
    flags = os.O_RDONLY
    if path.is_dir():
        flags |= getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def require_plain_name(name: str) -> None:
    if not name or name in {".", ".."} or Path(name).name != name:
        raise RuntimeError("manifest contains an unsafe filename")


def require_real_file(path: Path, *, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"{label} is not a regular file: {path.name}")


def sqlite_read_only_uri(path: Path) -> str:
    return f"{path.resolve(strict=True).as_uri()}?mode=ro"


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        safe_mode(path, 0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another Codex state staging run is active") from exc
        yield


def sqlite_metadata(path: Path) -> dict[str, Any]:
    require_real_file(path, label="SQLite database")
    uri = sqlite_read_only_uri(path)
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed for {path.name}: {integrity}")
        table_count = connection.execute(
            "SELECT count(*) FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
        page_count = connection.execute("PRAGMA page_count").fetchone()[0]
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
    return {
        "integrity": integrity,
        "table_count": table_count,
        "page_count": page_count,
        "user_version": user_version,
    }


def backup_database(source: Path, destination: Path) -> dict[str, Any]:
    require_real_file(source, label="source database")
    source_uri = sqlite_read_only_uri(source)
    with sqlite3.connect(source_uri, uri=True) as source_connection:
        source_connection.execute("PRAGMA query_only=ON")
        source_connection.execute("PRAGMA busy_timeout=30000")
        with sqlite3.connect(destination) as destination_connection:
            source_connection.backup(destination_connection, pages=1024, sleep=0.01)
    safe_mode(destination, 0o600)
    fsync_path(destination)
    metadata = sqlite_metadata(destination)
    metadata.update(
        {
            "sha256": sha256_file(destination),
            "size_bytes": destination.stat().st_size,
            "source_mtime_ns": source.stat().st_mtime_ns,
        }
    )
    return metadata


def copy_index_file(source: Path, destination: Path) -> dict[str, Any]:
    # Snapshot exactly the bytes visible at open time. JSONL is append-only; a
    # trailing partial line is removed so the staged prefix remains parseable.
    require_real_file(source, label="source index")
    source_size = source.stat().st_size
    if source_size > MAX_INDEX_BYTES:
        raise RuntimeError(f"state index exceeds private staging limit: {source.name}")
    data = source.read_bytes()
    if len(data) > MAX_INDEX_BYTES:
        raise RuntimeError(f"state index grew beyond private staging limit: {source.name}")
    if source.suffix == ".jsonl" and data and not data.endswith(b"\n"):
        newline = data.rfind(b"\n")
        data = data[: newline + 1] if newline >= 0 else b""
    destination.write_bytes(data)
    safe_mode(destination, 0o600)
    fsync_path(destination)
    return {
        "sha256": sha256_file(destination),
        "size_bytes": destination.stat().st_size,
        "source_mtime_ns": source.stat().st_mtime_ns,
    }


def validate_index_file(path: Path) -> None:
    require_real_file(path, label="staged index")
    if path.suffix == ".jsonl":
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if line.strip():
                    try:
                        json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(
                            f"invalid JSONL in {path.name} at line {line_number}"
                        ) from exc
    elif path.suffix == ".json":
        json.loads(path.read_text(encoding="utf-8"))
    elif path.suffix == ".toml":
        tomllib.loads(path.read_text(encoding="utf-8"))


def verify_snapshot(snapshot_dir: Path) -> dict[str, Any]:
    if snapshot_dir.is_symlink() or not snapshot_dir.is_dir():
        raise RuntimeError("Codex state snapshot must be a regular directory")
    manifest_path = snapshot_dir / "manifest.json"
    require_real_file(manifest_path, label="snapshot manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != 1:
        raise RuntimeError("unsupported Codex state manifest version")

    databases = manifest.get("databases", {})
    if not isinstance(databases, dict) or not databases:
        raise RuntimeError("manifest contains no databases")
    database_root = snapshot_dir / "databases"
    index_root = snapshot_dir / "indexes"
    if database_root.is_symlink() or not database_root.is_dir():
        raise RuntimeError("snapshot database directory is invalid")
    if index_root.is_symlink() or not index_root.is_dir():
        raise RuntimeError("snapshot index directory is invalid")
    for name, expected in databases.items():
        require_plain_name(name)
        if not isinstance(expected, dict):
            raise RuntimeError(f"invalid manifest record for database: {name}")
        path = database_root / name
        require_real_file(path, label="staged database")
        if sha256_file(path) != expected["sha256"]:
            raise RuntimeError(f"hash mismatch for staged database: {name}")
        if path.stat().st_size != expected["size_bytes"]:
            raise RuntimeError(f"size mismatch for staged database: {name}")
        metadata = sqlite_metadata(path)
        if metadata["integrity"] != "ok":
            raise RuntimeError(f"integrity failure for staged database: {name}")
        for field in ("table_count", "page_count", "user_version"):
            if metadata[field] != expected[field]:
                raise RuntimeError(f"metadata mismatch for staged database: {name}")

    indexes = manifest.get("indexes", {})
    if not isinstance(indexes, dict):
        raise RuntimeError("manifest indexes are invalid")
    for name, expected in indexes.items():
        require_plain_name(name)
        if not isinstance(expected, dict):
            raise RuntimeError(f"invalid manifest record for index: {name}")
        path = index_root / name
        require_real_file(path, label="staged index")
        if sha256_file(path) != expected["sha256"]:
            raise RuntimeError(f"hash mismatch for staged index: {name}")
        if path.stat().st_size != expected["size_bytes"]:
            raise RuntimeError(f"size mismatch for staged index: {name}")
        validate_index_file(path)

    return {
        "status": "ok",
        "snapshot_id": manifest["snapshot_id"],
        "database_count": len(databases),
        "index_count": len(indexes),
    }


def atomic_publish(incoming: Path, stage_root: Path) -> Path:
    current = stage_root / "current"
    previous = stage_root / "previous"
    for path in (current, previous):
        if path.is_symlink():
            raise RuntimeError(f"refusing symlink in staging publication: {path.name}")
    if previous.exists():
        shutil.rmtree(previous)
    if current.exists():
        os.replace(current, previous)
    try:
        os.replace(incoming, current)
    except Exception:
        if previous.exists() and not current.exists():
            os.replace(previous, current)
        raise
    fsync_path(stage_root)
    return current


def stage(codex_home: Path, stage_root: Path) -> dict[str, Any]:
    if not codex_home.is_dir():
        raise RuntimeError(f"Codex home does not exist: {codex_home}")
    if stage_root.is_symlink():
        raise RuntimeError("Codex state staging root must not be a symlink")
    databases = sorted(codex_home.glob("*.sqlite"))
    for database in databases:
        require_real_file(database, label="source database")
    if not databases:
        raise RuntimeError(f"no SQLite databases found under {codex_home}")

    stage_root.mkdir(parents=True, exist_ok=True)
    safe_mode(stage_root, 0o700)
    snapshot_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    incoming = Path(tempfile.mkdtemp(prefix=".incoming-", dir=stage_root))
    safe_mode(incoming, 0o700)
    database_dir = incoming / "databases"
    index_dir = incoming / "indexes"
    database_dir.mkdir(mode=0o700)
    index_dir.mkdir(mode=0o700)

    manifest: dict[str, Any] = {
        "version": 1,
        "tool": "edsys-codex-state-stage",
        "snapshot_id": snapshot_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": str(codex_home),
        "databases": {},
        "indexes": {},
    }
    try:
        for source in databases:
            destination = database_dir / source.name
            manifest["databases"][source.name] = backup_database(source, destination)

        for name in INDEX_FILES:
            source = codex_home / name
            if source.is_file():
                manifest["indexes"][name] = copy_index_file(source, index_dir / name)

        manifest_path = incoming / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        safe_mode(manifest_path, 0o600)
        fsync_path(manifest_path)
        fsync_path(database_dir)
        fsync_path(index_dir)
        fsync_path(incoming)
        verify_snapshot(incoming)
        current = atomic_publish(incoming, stage_root)
        return verify_snapshot(current)
    except Exception:
        if incoming.exists():
            shutil.rmtree(incoming)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--codex-home", type=Path, default=DEFAULT_CODEX_HOME, help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--stage-root", type=Path, default=DEFAULT_STAGE_ROOT, help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--lock-file", type=Path, default=DEFAULT_LOCK_FILE, help=argparse.SUPPRESS
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("stage", help="create and atomically publish a new snapshot")
    verify = subparsers.add_parser("verify", help="verify an existing staged snapshot")
    verify.add_argument("snapshot_dir", nargs="?", type=Path)
    return parser.parse_args()


def main() -> int:
    os.umask(0o077)
    args = parse_args()
    try:
        if args.command == "stage":
            with exclusive_lock(args.lock_file):
                result = stage(args.codex_home, args.stage_root)
        else:
            result = verify_snapshot(args.snapshot_dir or args.stage_root / "current")
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:  # concise systemd-safe failure; no state content
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
