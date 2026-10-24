#!/usr/bin/env python3
"""Private, bounded originals recovery staging; never alters an application/source.

The same reviewed file runs locally and, over the existing pinned SSH route,
as a stream-only remote worker. Runtime manifests/configuration contain private
data and must remain outside Git, public logs, Obsidian and RAG.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import select
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid


CHUNK = 1024 * 1024
MAX_FILES = 200_000
MAX_JSON = 64 * CHUNK
MAX_ARCHIVE = 64 * 1024**3
RESERVE = 1024**3
GENERATION = re.compile(r"\d{8}T\d{6}Z-[a-f0-9]{12}\Z")
SNAPSHOT = re.compile(r"[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+\Z")
IMMICH_ROOT = Path("/srv/data/immich/upload")
LEGACY_LIBRARY = Path("/srv/media/immich/library")
CONTAINERS = (
    "immich-server", "immich-postgres", "immich-redis", "immich-machine-learning"
)
END = time.monotonic() + 900


class Refused(RuntimeError):
    """Only fixed reason identifiers are safe for stdout/stderr."""


def remaining() -> float:
    value = END - time.monotonic()
    if value <= 0:
        raise Refused("deadline_exceeded")
    return value


def utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_path(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    for parent in reversed((path, *path.parents)):
        if parent.is_symlink():
            raise Refused("symlink_path")
    return path


def private_dir(path: Path) -> Path:
    path = safe_path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    s = path.stat()
    if s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) & 0o077:
        raise Refused("unsafe_private_directory")
    return path


def sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path: Path, value: dict) -> None:
    safe_path(path)
    fd, name = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, sort_keys=True, separators=(",", ":"))
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        sync_dir(path.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name)


def metadata(path: Path) -> dict:
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode):
        raise Refused("nonregular_original")
    return {
        "size": s.st_size, "mtime_ns": s.st_mtime_ns, "ctime_ns": s.st_ctime_ns,
        "inode": s.st_ino, "device": s.st_dev, "uid": s.st_uid,
        "gid": s.st_gid, "mode": stat.S_IMODE(s.st_mode),
    }


def inventory(roots: dict[str, Path]) -> dict:
    result = {}
    for label, root in roots.items():
        root = safe_path(root)
        if not root.is_dir():
            raise Refused("required_original_root_missing")
        for current, dirs, files in os.walk(root, followlinks=False):
            remaining()
            for name in dirs:
                if (Path(current) / name).is_symlink():
                    raise Refused("symlink_original_directory")
            dirs.sort()
            for name in sorted(files):
                p = Path(current) / name
                archive_name = f"{label}/{p.relative_to(root).as_posix()}"
                result[archive_name] = {"source": str(p), **metadata(p)}
                if len(result) > MAX_FILES:
                    raise Refused("file_count_limit")
    return result


class HashReader:
    def __init__(self, source):
        self.source = source
        self.hash = hashlib.sha256()
        self.bytes = 0

    def read(self, size=-1):
        remaining()
        data = self.source.read(size)
        self.hash.update(data)
        self.bytes += len(data)
        if self.bytes > MAX_ARCHIVE:
            raise Refused("archive_size_limit")
        return data


class BoundedWriter:
    def __init__(self, target, disk: Path | None = None):
        self.target, self.disk, self.bytes = target, disk, 0

    def write(self, data):
        remaining()
        self.bytes += len(data)
        if self.bytes > MAX_ARCHIVE:
            raise Refused("archive_size_limit")
        if self.disk and shutil.disk_usage(self.disk).free < RESERVE:
            raise Refused("disk_reserve_exhausted")
        return self.target.write(data)

    def flush(self):
        self.target.flush()


class Archive:
    def __init__(self, output, kind: str, details: dict, disk: Path | None = None):
        self.writer = BoundedWriter(output, disk)
        self.tar = tarfile.open(fileobj=self.writer, mode="w|", format=tarfile.PAX_FORMAT)
        self.manifest = {"format": 1, "kind": kind, "created_utc": utc(),
                         "details": details, "files": {}}

    def add(self, name: str, source: Path, expected: dict | None = None):
        if name in self.manifest["files"] or name == "MANIFEST.json":
            raise Refused("duplicate_archive_member")
        before = metadata(source)
        if expected is not None and before != {k: expected[k] for k in before}:
            raise Refused("source_changed_before_copy")
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as f:
            actual = os.fstat(f.fileno())
            if (actual.st_ino, actual.st_dev) != (before["inode"], before["device"]):
                raise Refused("source_replaced_before_copy")
            reader = HashReader(f)
            info = tarfile.TarInfo(name)
            info.size, info.mode = before["size"], before["mode"]
            info.uid, info.gid = before["uid"], before["gid"]
            info.mtime = before["mtime_ns"] // 1_000_000_000
            self.tar.addfile(info, reader)
            if reader.bytes != before["size"] or metadata(source) != before:
                raise Refused("source_changed_during_copy")
        self.manifest["files"][name] = {**before, "source": str(source),
                                         "sha256": reader.hash.hexdigest()}

    def json(self, name: str, value):
        content = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        if len(content) > MAX_JSON:
            raise Refused("configuration_size_limit")
        info = tarfile.TarInfo(name)
        info.size, info.mode = len(content), 0o600
        self.tar.addfile(info, io.BytesIO(content))
        self.manifest["files"][name] = {
            "size": len(content), "sha256": hashlib.sha256(content).hexdigest(),
        }

    def finish(self):
        data = json.dumps(self.manifest, sort_keys=True, separators=(",", ":")).encode()
        if len(data) > MAX_JSON:
            raise Refused("manifest_size_limit")
        info = tarfile.TarInfo("MANIFEST.json")
        info.size, info.mode = len(data), 0o600
        self.tar.addfile(info, io.BytesIO(data))
        self.tar.close()
        self.writer.flush()


def verify_archive(path: Path, kind: str) -> dict:
    found, manifest = {}, None
    with path.open("rb") as f:
        reader = HashReader(f)
        with tarfile.open(fileobj=reader, mode="r|") as archive:
            for member in archive:
                remaining()
                name = member.name
                pure = PurePosixPath(name)
                if (not member.isreg() or pure.is_absolute() or ".." in pure.parts
                        or "\\" in name or name in found or manifest is not None):
                    raise Refused("unsafe_archive_member")
                if member.size < 0 or member.size > MAX_ARCHIVE:
                    raise Refused("archive_size_limit")
                stream = archive.extractfile(member)
                if name == "MANIFEST.json":
                    if member.size > MAX_JSON:
                        raise Refused("manifest_size_limit")
                    manifest = json.loads(stream.read(MAX_JSON + 1))
                    continue
                h, size = hashlib.sha256(), 0
                while data := stream.read(CHUNK):
                    remaining()
                    h.update(data)
                    size += len(data)
                found[name] = {"size": size, "sha256": h.hexdigest()}
                if len(found) > MAX_FILES:
                    raise Refused("file_count_limit")
        while reader.read(CHUNK):
            pass
        total, digest = reader.bytes, reader.hash.hexdigest()
    if (not isinstance(manifest, dict) or manifest.get("format") != 1
            or manifest.get("kind") != kind or not found
            or set(found) != set(manifest.get("files", {}))):
        raise Refused("missing_or_inconsistent_manifest")
    for name, actual in found.items():
        recorded = manifest["files"][name]
        if any(actual[k] != recorded.get(k) for k in ("size", "sha256")):
            raise Refused("archive_hash_mismatch")
    if kind == "immich" and not {"database.dump", "roles.sql", "recovery-config.json",
                                 "checkpoint.json"}.issubset(found):
        raise Refused("required_recovery_member_missing")
    return {"sha256": digest, "bytes": total, "members": len(found),
            "details": manifest.get("details", {})}


def command(args, *, output=None, data=None, input_file=None, timeout=None,
            failure_reason="read_command_failed") -> bytes:
    # Config/metadata output is captured privately on disk before a bounded
    # read. Database streams use file descriptors, never read() into RAM.
    with tempfile.TemporaryFile() as errors, tempfile.TemporaryFile() as captured:
        options = {"input": data} if input_file is None else {"stdin": input_file}
        r = subprocess.run(args, stdout=output or captured, stderr=errors,
                           timeout=min(remaining(), timeout or remaining()),
                           check=False, **options)
        if r.returncode:
            raise Refused(failure_reason)
        if output is not None:
            return b""
        if captured.tell() > MAX_JSON:
            raise Refused("command_output_limit")
        captured.seek(0)
        return captured.read(MAX_JSON + 1)


def docker_inspect() -> tuple[list, dict]:
    rows = json.loads(command(["docker", "inspect", *CONTAINERS],
                              failure_reason="container_inspection_failed"))
    by_name = {r["Name"].lstrip("/"): r for r in rows}
    if set(by_name) != set(CONTAINERS) or not all(r["State"]["Running"] for r in rows):
        raise Refused("required_application_container_missing")
    env = dict(v.split("=", 1) for v in by_name["immich-postgres"]["Config"]["Env"]
               if "=" in v)
    db = {"user": env.get("POSTGRES_USER", "postgres"),
          "database": env.get("POSTGRES_DB", "immich")}
    return rows, db


def configuration_snapshot(rows: list) -> list:
    # Docker builds Mounts from a map: its response order is not stable.
    # Preserve all values (and environment ordering), canonicalize only the
    # unordered container/mount collections before comparing configuration.
    return [{"Name": row.get("Name"), "Image": row.get("Image"),
             "Config": row.get("Config"),
             "Mounts": sorted(row.get("Mounts", []),
                              key=lambda mount: (mount.get("Destination", ""),
                                                 mount.get("Source", "")))}
            for row in sorted(rows, key=lambda value: value.get("Name", ""))]


def db_args(db: dict, executable="psql") -> list[str]:
    return ["docker", "exec", "-i", "-e",
            "PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=120000",
            "immich-postgres", executable, "-U", db["user"], "-d", db["database"]]


ASSETS_SQL = '''SELECT COALESCE(json_agg(json_build_object(
    'id',id,'path',"originalPath",'type',type) ORDER BY id),'[]')
    FROM asset WHERE "deletedAt" IS NULL;'''
LIBRARIES_SQL = 'SELECT count(*) FROM library WHERE "deletedAt" IS NULL;'


class DatabaseSnapshot:
    def __init__(self, db):
        self.db, self.errors = db, tempfile.TemporaryFile()
        self.process = None
        try:
            self.process = subprocess.Popen(
                db_args(db) + ["-qAtX", "-v", "ON_ERROR_STOP=1", "-P", "pager=off"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors)
            self.line("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;", expect=False)
            self.snapshot = self.line("SELECT pg_export_snapshot();")
            if not SNAPSHOT.fullmatch(self.snapshot):
                raise Refused("invalid_database_snapshot")
        except BaseException:
            self.close()
            raise

    def line(self, sql, expect=True):
        remaining()
        self.process.stdin.write((sql + "\n").encode())
        self.process.stdin.flush()
        if not expect:
            return None
        value = self.process.stdout.readline(MAX_JSON + 1)
        if not value or len(value) > MAX_JSON:
            raise Refused("database_checkpoint_read_failed")
        return value.decode().strip()

    def close(self):
        if self.process is not None and self.process.poll() is None:
            with contextlib.suppress(OSError, BrokenPipeError):
                self.process.stdin.write(b"ROLLBACK;\n\\q\n")
                self.process.stdin.flush()
            try:
                self.process.wait(timeout=min(5, remaining()))
            except (subprocess.TimeoutExpired, Refused):
                self.process.kill()
                self.process.wait(timeout=5)
        if self.process is not None:
            self.process.stdin.close()
            self.process.stdout.close()
        self.errors.close()


def reference_files(rows: list, assets: list) -> set[Path]:
    server = next(x for x in rows if x["Name"].lstrip("/") == "immich-server")
    mounts = server.get("Mounts", [])
    result = set()
    for asset in assets:
        p = PurePosixPath(asset["path"])
        if not p.is_absolute() or ".." in p.parts:
            raise Refused("unexpected_original_reference")
        choices = [m for m in mounts if m["Type"] == "bind"
                   and (str(p) == m["Destination"]
                        or str(p).startswith(m["Destination"].rstrip("/") + "/"))]
        if not choices:
            raise Refused("unmapped_original_reference")
        mount = max(choices, key=lambda m: len(m["Destination"]))
        root = Path(mount["Source"])
        if root not in (IMMICH_ROOT, LEGACY_LIBRARY):
            raise Refused("unapproved_original_root")
        source = safe_path(root / p.relative_to(mount["Destination"]))
        if not source.is_file():
            raise Refused("referenced_original_missing")
        result.add(source)
    return result


def original_roots() -> dict[str, Path]:
    return {"originals/upload": IMMICH_ROOT / "upload",
            "originals/library": IMMICH_ROOT / "library",
            "originals/profile": IMMICH_ROOT / "profile",
            "originals/legacy-library": LEGACY_LIBRARY}


def application_config(rows: list, checkpoint: DatabaseSnapshot) -> dict:
    images = {}
    for row in rows:
        image = json.loads(command(["docker", "image", "inspect", row["Image"]]))[0]
        images[row["Name"].lstrip("/")] = {
            k: image.get(k) for k in ("Id", "RepoDigests", "Os", "Architecture", "Created")
        }
    server = next(x for x in rows if x["Name"].lstrip("/") == "immich-server")
    labels = server["Config"].get("Labels") or {}
    configs = labels.get("com.docker.compose.project.config_files", "").split(",")
    directory = labels.get("com.docker.compose.project.working_dir", "")
    if not directory or not configs or not all(x and Path(x).is_absolute() for x in configs):
        raise Refused("compose_configuration_location_missing")
    compose_args = ["docker", "compose", "--project-directory", directory]
    for path in configs:
        safe_path(Path(path))
        compose_args += ["-f", path]
    resolved = command(compose_args + ["config", "--format", "json"],
                       failure_reason="resolved_compose_read_failed")
    if len(resolved) > MAX_JSON:
        raise Refused("configuration_size_limit")
    # Contains actual environment/credentials. Archive only; never log/print.
    return {"containers": [{k: x.get(k) for k in ("Name", "Image", "Config", "Mounts")}
                           for x in rows], "images": images,
            "resolved_compose": json.loads(resolved),
            "postgres_version": checkpoint.line("SHOW server_version;"),
            "postgres_extensions": json.loads(checkpoint.line(
                "SELECT json_agg(json_build_object('name',extname,'version',extversion) "
                "ORDER BY extname) FROM pg_extension;"))}


def remote_worker(output, preflight=False):
    rows, db = docker_inspect()
    checkpoint = DatabaseSnapshot(db)
    try:
        count = int(checkpoint.line('SELECT count(*) FROM asset WHERE "deletedAt" IS NULL;'))
        if not 0 < count <= 100_000 or int(checkpoint.line(LIBRARIES_SQL)) != 0:
            raise Refused("asset_scope_requires_review")
        assets = json.loads(checkpoint.line(ASSETS_SQL))
        references = reference_files(rows, assets)
        before = inventory(original_roots())
        if not references.issubset({Path(v["source"]) for v in before.values()}):
            raise Refused("original_reference_not_selected")
        estimated = sum(v["size"] for v in before.values())
        database_bytes = int(checkpoint.line("SELECT pg_database_size(current_database());"))
        if preflight:
            return {"assets": count, "original_files": len(before),
                    "original_bytes": estimated, "database_bytes": database_bytes}
        with tempfile.TemporaryDirectory(prefix="edsys-immich-export-") as name:
            temporary = Path(name)
            if shutil.disk_usage(temporary).free < 2 * database_bytes + RESERVE:
                raise Refused("remote_database_export_space_insufficient")
            dump = temporary / "database.dump"
            with dump.open("xb") as f:
                command(db_args(db, "pg_dump") + ["--format=custom", "--snapshot",
                                                   checkpoint.snapshot], output=f,
                        failure_reason="postgres_dump_failed")
                f.flush()
                os.fsync(f.fileno())
            with dump.open("rb") as f:
                command(["docker", "exec", "-i", "immich-postgres", "pg_restore",
                         "--file=/dev/null"], input_file=f,
                        failure_reason="postgres_dump_decode_failed")
            roles = temporary / "roles.sql"
            roles.write_bytes(command(["docker", "exec", "immich-postgres", "pg_dumpall",
                                       "-U", db["user"], "--globals-only"],
                                      failure_reason="postgres_roles_dump_failed"))
            config = application_config(rows, checkpoint)
            archive = Archive(output, "immich", {"assets": count,
                              "original_files": len(before), "database_snapshot": "shared"})
            archive.add("database.dump", dump)
            archive.add("roles.sql", roles)
            archive.json("recovery-config.json", config)
            archive.json("checkpoint.json", {"assets": assets,
                         "consistency": "one exported PostgreSQL snapshot; stable original files"})
            for arcname, value in sorted(before.items()):
                archive.add(arcname, Path(value["source"]), value)
            if inventory(original_roots()) != before:
                raise Refused("original_tree_changed_during_copy")
            current_assets = json.loads(command(db_args(db) + ["-qAtX", "-c", ASSETS_SQL]))
            if current_assets != assets:
                raise Refused("original_references_changed_during_copy")
            if int(command(db_args(db) + ["-qAtX", "-c", LIBRARIES_SQL])) != 0:
                raise Refused("external_library_scope_changed")
            current_rows, _ = docker_inspect()
            if configuration_snapshot(current_rows) != configuration_snapshot(rows):
                raise Refused("application_configuration_changed")
            archive.finish()
    finally:
        checkpoint.close()


def remote_reason(data: bytes) -> str:
    # Only the worker's fixed identifier crosses the private-error boundary.
    # SSH banners, hostnames, command text and arbitrary provider output do not.
    for line in reversed(data.splitlines()):
        try:
            result = json.loads(line)
            reason = result.get("reason")
            if (result.get("status") == "failed" and isinstance(reason, str)
                    and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", reason)):
                return "remote_" + reason
        except (ValueError, AttributeError):
            pass
    return "remote_collection_failed"


def ssh_run(args, *, output=None, preflight=False):
    remote = ("sudo -n timeout --signal=TERM --kill-after=5s 840s "
              "nice -n 15 ionice -c 2 -n 7 python3 - "
              + ("_remote-preflight" if preflight else "_remote"))
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o",
            "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={args.known_hosts}",
            "-i", args.ssh_key, "-o", "IdentitiesOnly=yes", args.ssh_target, remote]
    source = Path(__file__).read_bytes()
    if output is None:
        return command(argv, data=source)
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=errors, start_new_session=True)
        try:
            process.stdin.write(source)
            process.stdin.close()
            writer = BoundedWriter(output, Path(args.stage_root))
            while True:
                ready, _, _ = select.select([process.stdout], [], [], remaining())
                if not ready:
                    raise Refused("deadline_exceeded")
                data = os.read(process.stdout.fileno(), CHUNK)
                if not data:
                    break
                writer.write(data)
            if process.wait(timeout=remaining()) != 0:
                errors.seek(0)
                raise Refused(remote_reason(errors.read(4096)))
            writer.flush()
            return b""
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)


def read_current(root: Path) -> dict:
    path = safe_path(root / "CURRENT.json")
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    if (not isinstance(value, dict) or not GENERATION.fullmatch(value.get("current", ""))
            or (value.get("previous") and not GENERATION.fullmatch(value["previous"]))):
        raise Refused("invalid_current_pointer")
    return value


def publish(root: Path, incoming: Path, summary: dict) -> dict:
    old = read_current(root)
    generation = incoming.name
    if not GENERATION.fullmatch(generation):
        raise Refused("invalid_generation")
    atomic_json(incoming / "generation.json", summary)
    generations = private_dir(root / "generations")
    # Validate cleanup candidates before committing the new pointer. A bad
    # sibling must never cause a failed attempt to replace last-good state.
    cleanup = []
    for candidate in generations.iterdir():
        if candidate.name != old.get("current") and GENERATION.fullmatch(candidate.name):
            safe_path(candidate)
            s = candidate.stat()
            if (not candidate.is_dir() or s.st_uid != os.geteuid()
                    or stat.S_IMODE(s.st_mode) & 0o077):
                raise Refused("unsafe_old_staging_generation")
            cleanup.append(candidate)
    destination = generations / generation
    os.rename(incoming, destination)
    sync_dir(generations)
    pointer = {"current": generation, "previous": old.get("current"),
               "completed_utc": summary["completed_utc"]}
    atomic_json(root / "CURRENT.json", pointer)
    # Only our canonical completed, owned generations are staging retention.
    # Never remove original sources or encrypted repository snapshots.
    for candidate in cleanup:
        with contextlib.suppress(OSError):
            shutil.rmtree(candidate)
    return pointer


def stage(args) -> dict:
    root = private_dir(Path(args.stage_root))
    lockpath = safe_path(root / ".lock")
    with lockpath.open("a") as lock:
        os.chmod(lockpath, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused("stage_already_running") from None
        pending = None
        try:
            old = read_current(root)
            local_before = inventory({"images": Path(args.image_root)})
            if not local_before:
                raise Refused("empty_image_source_requires_review")
            remote = json.loads(ssh_run(args, preflight=True))
            estimate = (sum(v["size"] for v in local_before.values())
                        + remote["original_bytes"] + remote["database_bytes"] + 64 * CHUNK)
            if estimate > MAX_ARCHIVE or shutil.disk_usage(root).free < 3 * estimate + RESERVE:
                raise Refused("insufficient_space_for_three_stages_and_reserve")
            pending_root = private_dir(root / ".incoming")
            generation = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
            pending = private_dir(pending_root / generation)
            local = pending / "images.tar"
            with local.open("xb") as f:
                archive = Archive(f, "images", {"files": len(local_before)}, pending)
                for name, value in sorted(local_before.items()):
                    archive.add(name, Path(value["source"]), value)
                if inventory({"images": Path(args.image_root)}) != local_before:
                    raise Refused("image_tree_changed_during_copy")
                archive.finish()
                f.flush()
                os.fsync(f.fileno())
            remote_path = pending / "immich.tar"
            with remote_path.open("xb") as f:
                ssh_run(args, output=f)
                f.flush()
                os.fsync(f.fileno())
            image_proof = verify_archive(local, "images")
            immich_proof = verify_archive(remote_path, "immich")
            if inventory({"images": Path(args.image_root)}) != local_before:
                raise Refused("image_tree_changed_before_publication")
            summary = {"format": 1, "completed_utc": utc(), "images": image_proof,
                       "immich": immich_proof, "offsite": "not_verified_by_collection"}
            pointer = publish(root, pending, summary)
            pending = None
            health = {"status": "healthy", "reason": "verified_complete_stage",
                      "attempted_utc": utc(), "last_success_utc": summary["completed_utc"],
                      "images": len(local_before), "immich_assets": immich_proof["details"]["assets"]}
            atomic_json(root / "status.json", health)
            return {**health, "generation": pointer["current"]}
        except BaseException as exc:
            reason = str(exc) if isinstance(exc, Refused) else type(exc).__name__
            previous = locals().get("old", {})
            atomic_json(root / "status.json", {"status": "failed", "reason": reason,
                        "attempted_utc": utc(), "last_success_utc": previous.get("completed_utc")})
            raise
        finally:
            if pending is not None and pending.exists():
                shutil.rmtree(pending)


def verify_stage(root: Path) -> dict:
    root = safe_path(root)
    current = read_current(root)
    directory = safe_path(root / "generations" / current["current"])
    recorded = json.loads((directory / "generation.json").read_text())
    for name, kind in (("images.tar", "images"), ("immich.tar", "immich")):
        proof = verify_archive(safe_path(directory / name), kind)
        for field in ("sha256", "bytes", "members", "details"):
            if proof[field] != recorded[kind][field]:
                raise Refused("generation_manifest_mismatch")
    return {"status": "verified", "completed_utc": recorded["completed_utc"],
            "images": recorded["images"]["details"]["files"],
            "immich_assets": recorded["immich"]["details"]["assets"]}


def main():
    global END
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stage", "verify", "_remote", "_remote-preflight"))
    parser.add_argument("--stage-root", default="/srv/edsys-backup/staging/image-originals")
    parser.add_argument("--image-root", default="/mnt/media/images")
    parser.add_argument("--ssh-target", default="jeremy@192.168.50.78")
    parser.add_argument("--ssh-key", default="/home/jeremy/.ssh/id_ed25519")
    parser.add_argument("--known-hosts", default="/etc/edsys-backup/known_hosts")
    parser.add_argument("--deadline-seconds", type=int, default=900)
    args = parser.parse_args()
    # Activated only after the existing instance has moved to the guarded local
    # SSD. An absent activation file preserves the original remote collector.
    if args.action in ("stage", "verify") and Path("/etc/edsys-backup/immich-local.json").exists():
        helper = Path(__file__).with_name("edsys-immich-local.py")
        return subprocess.call([sys.executable, str(helper), args.action,
                                "--stage-root", args.stage_root])
    if not 1 <= args.deadline_seconds <= 900:
        parser.error("deadline must be between 1 and 900 seconds")
    END = time.monotonic() + args.deadline_seconds
    def interrupted(_signum, _frame):
        raise Refused("interrupted")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        if args.action == "_remote":
            remote_worker(sys.stdout.buffer)
            return 0
        if args.action == "_remote-preflight":
            result = remote_worker(None, preflight=True)
        elif args.action == "stage":
            result = stage(args)
        else:
            result = verify_stage(Path(args.stage_root))
        print(json.dumps(result, sort_keys=True))
        return 0
    except BaseException as exc:
        reason = str(exc) if isinstance(exc, Refused) else type(exc).__name__
        print(json.dumps({"status": "failed", "reason": reason}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
