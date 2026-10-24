#!/usr/bin/env python3
"""Guarded local Immich recovery; originals stream directly into encrypted Restic.

Private configuration and manifests must never be printed. This is called by
the existing daily backup, not a separate scheduler. No application is stopped.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid

CONFIG = Path("/etc/edsys-backup/immich-local.json")
MEDIA_ROOT = Path("/mnt/immich-library")
INTERNAL_ROOT = Path("/mnt/ai-store")
UPLOAD = MEDIA_ROOT / "upload"
LEGACY = MEDIA_ROOT / "legacy-library"
RECOVERY_ROOT = Path("/srv/edsys-backup/staging/image-originals")
GENERATION = re.compile(r"\d{8}T\d{6}Z-[a-f0-9]{12}\Z")
SNAPSHOT = re.compile(r"[0-9a-f]{8,64}\Z")


class Refused(RuntimeError):
    """Only fixed reason identifiers may escape to stdout/stderr."""


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def safe(path: Path):
    path = Path(os.path.abspath(path))
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise Refused("symlink_path")
    return path


def private(path: Path):
    safe(path).mkdir(mode=0o700, parents=True, exist_ok=True)
    s = path.stat()
    if s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) & 0o077:
        raise Refused("unsafe_private_directory")
    return path


def atomic(path: Path, value):
    target = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    with target.open("x", encoding="utf-8") as f:
        json.dump(value, f, sort_keys=True, separators=(",", ":"))
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(target, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def command(argv, *, output=None, input_file=None, timeout=900):
    result = subprocess.run(argv, stdin=input_file, stdout=output or subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    if result.returncode:
        raise Refused("private_command_failed")
    return result.stdout


def load_config(path=CONFIG):
    safe(path)
    s = path.stat()
    if s.st_uid != 0 or stat.S_IMODE(s.st_mode) != 0o600 or not stat.S_ISREG(s.st_mode):
        raise Refused("unsafe_local_backup_configuration")
    return validate_policy(json.loads(path.read_text()))


def validate_policy(value):
    if value.get("enabled") is not True or value.get("format") != 1:
        raise Refused("local_backup_not_activated")
    for key in ("media_uuid", "nvme_uuid"):
        expected = value.get(key)
        if not isinstance(expected, str):
            raise Refused("expected_storage_identity_missing")
        try:
            if str(uuid.UUID(expected)) != expected:
                raise ValueError
        except ValueError:
            raise Refused("expected_storage_identity_invalid") from None
    if value["media_uuid"] == value["nvme_uuid"]:
        raise Refused("separate_storage_identities_required")
    deadline = value.get("backup_timeout_seconds", 14400)
    if isinstance(deadline, bool) or not isinstance(deadline, int) or not 60 <= deadline <= 21600:
        raise Refused("invalid_backup_deadline")
    return value


def mounted_uuid(path: Path, expected: str):
    safe(path)
    result = json.loads(command(["findmnt", "--json", "--mountpoint", str(path),
                                 "--output", "TARGET,UUID,FSTYPE"]))
    rows = result.get("filesystems", [])
    if len(rows) != 1 or rows[0].get("target") != str(path) or rows[0].get("uuid") != expected:
        raise Refused("required_storage_uuid_missing")
    if rows[0].get("fstype") != "ext4":
        raise Refused("unexpected_storage_filesystem")


def storage_guard(config):
    config = validate_policy(config)
    mounted_uuid(MEDIA_ROOT, config["media_uuid"])
    mounted_uuid(INTERNAL_ROOT, config["nvme_uuid"])
    if not UPLOAD.is_dir() or not (INTERNAL_ROOT / "immich/config").is_dir():
        raise Refused("required_application_storage_missing")
    if UPLOAD.stat().st_dev != MEDIA_ROOT.stat().st_dev or (INTERNAL_ROOT / "immich/config").stat().st_dev != INTERNAL_ROOT.stat().st_dev:
        raise Refused("unexpected_nested_storage_mount")


def legacy_module():
    source = Path(__file__).with_name("edsys-image-originals.py")
    spec = importlib.util.spec_from_file_location("immich_legacy_recovery", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.END = time.monotonic() + 900
    # The local reference list scales independently of the legacy archive cap.
    module.MAX_JSON = 256 * 1024**2
    return module


def map_reference(path: str, rows: list):
    p = PurePosixPath(path)
    if not p.is_absolute() or ".." in p.parts:
        raise Refused("unexpected_original_reference")
    server = next(r for r in rows if r["Name"].lstrip("/") == "immich-server")
    choices = [m for m in server["Mounts"] if m["Type"] == "bind" and
               (str(p) == m["Destination"] or str(p).startswith(m["Destination"].rstrip("/") + "/"))]
    if not choices:
        raise Refused("unmapped_original_reference")
    mount = max(choices, key=lambda m: len(m["Destination"]))
    root = Path(mount["Source"])
    if root not in (UPLOAD, LEGACY):
        raise Refused("unapproved_original_root")
    result = safe(root / p.relative_to(mount["Destination"]))
    allowed = [UPLOAD / name for name in ("upload", "library", "profile")] + [LEGACY]
    if not any(result.is_relative_to(root) for root in allowed):
        raise Refused("reference_is_not_an_original")
    if not result.is_file():
        raise Refused("referenced_original_missing")
    if result.stat().st_dev != MEDIA_ROOT.stat().st_dev:
        raise Refused("original_is_on_wrong_filesystem")
    return result


def original_roots():
    roots = [UPLOAD / name for name in ("upload", "library", "profile")]
    if LEGACY.is_dir():
        roots.append(LEGACY)
    for root in roots:
        safe(root)
        if not root.is_dir():
            raise Refused("original_root_missing")
    # Preserve the app's storage markers for otherwise regenerable folders.
    # Thumbnails/transcoded payloads remain outside the original-file snapshot.
    for directory in ("backups", "encoded-video", "thumbs"):
        marker = safe(UPLOAD / directory / ".immich")
        if marker.is_file():
            roots.append(marker)
    return roots


def stage(root: Path, config: dict):
    storage_guard(config)
    legacy = legacy_module()
    rows, db = legacy.docker_inspect()
    postgres = next(r for r in rows if r["Name"].lstrip("/") == "immich-postgres")
    if not any(m["Type"] == "bind" and m["Destination"] == "/var/lib/postgresql/data" and
               m["Source"] == str(INTERNAL_ROOT / "immich/postgres") for m in postgres["Mounts"]):
        raise Refused("database_is_not_on_expected_local_storage")
    checkpoint = legacy.DatabaseSnapshot(db)
    incoming = private(private(root / ".incoming") / ("local-" + uuid.uuid4().hex))
    try:
        if int(checkpoint.line(legacy.LIBRARIES_SQL)) != 0:
            raise Refused("external_libraries_require_backup_review")
        assets = json.loads(checkpoint.line(legacy.ASSETS_SQL))
        if not assets or len(assets) > 1_000_000:
            raise Refused("asset_scope_requires_review")
        refs = {str(map_reference(a["path"], rows)): None for a in assets}
        for path in refs:
            refs[path] = legacy.metadata(Path(path))
        db_size = int(checkpoint.line("SELECT pg_database_size(current_database());"))
        if shutil.disk_usage(incoming).free < 2 * db_size + 1024**3:
            raise Refused("database_stage_space_insufficient")
        dump = incoming / "database.dump"
        with dump.open("xb") as f:
            legacy.command(legacy.db_args(db, "pg_dump") + ["--format=custom", "--snapshot", checkpoint.snapshot], output=f)
            f.flush()
            os.fsync(f.fileno())
        with dump.open("rb") as f:
            legacy.command(["docker", "exec", "-i", "immich-postgres", "pg_restore", "--file=/dev/null"], input_file=f)
        (incoming / "roles.sql").write_bytes(legacy.command(["docker", "exec", "immich-postgres", "pg_dumpall", "-U", db["user"], "--globals-only"]))
        atomic(incoming / "recovery-config.json", legacy.application_config(rows, checkpoint))
        atomic(incoming / "references.json", {"assets": assets, "files": refs,
               "consistency": "database exported first; every reference must exist in accepted original-file snapshot"})
        images = legacy.inventory({"images": Path("/mnt/media/images")})
        if not images:
            raise Refused("local_image_scope_empty")
        with (incoming / "images.tar").open("xb") as f:
            archive = legacy.Archive(f, "images", {"files": len(images)}, incoming)
            for name, value in sorted(images.items()):
                archive.add(name, Path(value["source"]), value)
            archive.finish()
            f.flush()
            os.fsync(f.fileno())
        if legacy.inventory({"images": Path("/mnt/media/images")}) != images:
            raise Refused("local_image_source_changed")
        legacy.verify_archive(incoming / "images.tar", "images")
        current_rows, _ = legacy.docker_inspect()
        if legacy.configuration_snapshot(rows) != legacy.configuration_snapshot(current_rows):
            raise Refused("application_configuration_changed")
        generation = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
        atomic(incoming / "generation.json", {"format": 1, "generation": generation,
               "completed_utc": utc(), "immich_assets": len(assets), "images": len(images),
               "files": {p.name: {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
                         for p in incoming.iterdir() if p.is_file()}})
        destination = private(root / "local-generations") / generation
        os.rename(incoming, destination)
        atomic(root / "CURRENT_LOCAL.json", {"current": generation, "completed_utc": utc()})
        health = {"status": "staged", "reason": "local_database_staged_originals_snapshot_pending",
                  "attempted_utc": utc(), "generation": generation,
                  "images": len(images), "immich_assets": len(assets)}
        atomic(root / "status.json", health)
        return health
    finally:
        checkpoint.close()


def current_generation(root: Path):
    pointer = json.loads((safe(root) / "CURRENT_LOCAL.json").read_text())
    name = pointer.get("current", "")
    if not GENERATION.fullmatch(name):
        raise Refused("invalid_local_generation")
    path = safe(root / "local-generations" / name)
    manifest = json.loads((path / "generation.json").read_text())
    for name, expected in manifest["files"].items():
        if Path(name).name != name:
            raise Refused("invalid_generation_member")
        p = safe(path / name)
        if p.stat().st_size != expected["bytes"] or hashlib.sha256(p.read_bytes()).hexdigest() != expected["sha256"]:
            raise Refused("local_generation_hash_mismatch")
    return path, manifest


def verify_snapshot_entries(lines, references: dict):
    missing = set(references)
    for line in lines:
        row = json.loads(line)
        path = row.get("path")
        if path in references:
            if row.get("type") != "file" or row.get("size") != references[path]["size"]:
                raise Refused("snapshot_original_size_mismatch")
            missing.discard(path)
    if missing:
        raise Refused("snapshot_original_reference_missing")


def backup(root: Path, config: dict):
    storage_guard(config)
    generation, manifest = current_generation(root)
    staged_status = json.loads((root / "status.json").read_text())
    if staged_status.get("status") != "staged" or staged_status.get("generation") != generation.name:
        raise Refused("matching_fresh_database_stage_required")
    captured = dt.datetime.fromisoformat(manifest["completed_utc"].replace("Z", "+00:00"))
    age = (dt.datetime.now(dt.timezone.utc) - captured).total_seconds()
    if not 0 <= age <= 6 * 3600:
        raise Refused("database_stage_is_stale")
    refs = json.loads((generation / "references.json").read_text())["files"]
    legacy = legacy_module()
    for path, expected in refs.items():
        if legacy.metadata(safe(Path(path))) != expected:
            raise Refused("referenced_original_changed_since_database")
    roots = original_roots()
    log = private(root / "local-run-reports") / ("restic-" + uuid.uuid4().hex + ".private.jsonl")
    args = ["restic", "--retry-lock", "5m", "backup", "--json", "--tag", "immich-originals",
            "--tag", "immich-local-v1", "--exclude", str(root / ".incoming"),
            str(root / "local-generations"), str(INTERNAL_ROOT / "immich/config"), *map(str, roots)]
    # The private command output is never replayed into the public backup log.
    with log.open("xb") as f:
        command(args, output=f, timeout=config.get("backup_timeout_seconds", 14400))
    snapshot = None
    with log.open() as f:
        for line in f:
            row = json.loads(line)
            if row.get("message_type") == "summary":
                snapshot = row.get("snapshot_id")
    if not isinstance(snapshot, str) or not SNAPSHOT.fullmatch(snapshot):
        raise Refused("original_snapshot_identity_missing")
    process = subprocess.Popen(["restic", "ls", "--json", snapshot], stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True)
    try:
        required = {**refs, **{str(p): {"size": p.stat().st_size}
                              for p in generation.iterdir() if p.is_file()}}
        verify_snapshot_entries(process.stdout, required)
        if process.wait(timeout=60):
            raise Refused("snapshot_listing_failed")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
    storage_guard(config)
    for path, expected in refs.items():
        if legacy.metadata(safe(Path(path))) != expected:
            raise Refused("referenced_original_changed_during_backup")
    health = {"status": "healthy", "reason": "local_originals_and_database_snapshot_verified",
              "last_success_utc": manifest["completed_utc"], "snapshot_verified_utc": utc(),
              "attempted_utc": utc(), "snapshot_id": snapshot, "generation": generation.name,
              "images": manifest["images"], "immich_assets": manifest["immich_assets"]}
    atomic(root / "status.json", health)
    atomic(generation / "accepted-snapshot.json", health)
    # Only our small, completed local metadata generations are retained here.
    # Legacy archives and all encrypted snapshots remain untouched.
    old = sorted((root / "local-generations").iterdir(), key=lambda p: p.name, reverse=True)
    for candidate in old[2:]:
        if GENERATION.fullmatch(candidate.name) and candidate.is_dir() and not candidate.is_symlink():
            shutil.rmtree(candidate)
    return health


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stage", "backup", "verify"))
    parser.add_argument("--stage-root", default=str(RECOVERY_ROOT))
    args = parser.parse_args()
    root = private(Path(args.stage_root))
    try:
        config = load_config()
        with (root / ".local-lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.action == "stage":
                result = stage(root, config)
            elif args.action == "backup":
                result = backup(root, config)
            else:
                _, result = current_generation(root)
                result = {"status": "verified", "completed_utc": result["completed_utc"],
                          "images": result["images"], "immich_assets": result["immich_assets"]}
        print(json.dumps(result, sort_keys=True))
        return 0
    except BaseException as exc:
        reason = (str(exc) if exc.__class__.__name__ == "Refused" and
                  re.fullmatch(r"[a-z_]+", str(exc)) else type(exc).__name__)
        with contextlib.suppress(Exception):
            atomic(root / "status.json", {"status": "failed", "reason": reason, "attempted_utc": utc()})
        print(json.dumps({"status": "failed", "reason": reason}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
