#!/usr/bin/env python3
"""Reviewed, private network exports. No restore, reconfiguration or service control.

A private root-owned policy selects exact existing targets/roots/read commands.
UniFi/Aruba additionally require an explicitly approved, hash-pinned native
exporter; absence or incomplete exports are failures, never fresh protection.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import errno
import fcntl
import fnmatch
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import select
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from urllib.parse import quote_from_bytes

CHUNK = 1024 * 1024
MAX_JSON = 32 * CHUNK
MAX_BYTES = 2 * 1024**3
MAX_FILES = 100_000
RESERVE = 1024**3
FRESH_SECONDS = 26 * 3600
NATIVE_WORK_ROOT = Path("/var/lib/edsys-backup/network-native-work")
END = time.monotonic() + 900
REQUIRED = {"router", "router-host", "pihole-primary", "pihole-secondary", "unifi", "aruba"}
KINDS = {"router", "router-host", "pihole", "unifi", "aruba"}
GENERATION = re.compile(r"\d{8}T\d{6}Z-[a-f0-9]{12}\Z")
LABEL = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
REQUIRED_COMMANDS = {
    "router": {"nft-ruleset", "netplan", "interfaces", "routes", "policy-rules", "services", "packages"},
    "router-host": {"vm-config", "vm-command", "interfaces", "bridges", "packages"},
    "pihole": {"pihole-version", "core-version", "web-version", "unbound-version", "services", "packages"},
    "unifi": {"controller-inspect", "database-inspect", "software-images"},
    "aruba": set(),
}
REQUIRED_ROOTS = {
    "router": {"etc", "local", "dhcp-leases"}, "router-host": {"network", "vm-config"},
    "pihole": {"etc", "core", "web", "ftl-binary", "unbound-binary", "pihole-helpers",
               "pihole-cli", "unbound-helper", "package-services", "unbound-state"},
    "unifi": {"configuration"}, "aruba": set(),
}
SAFE_COMMAND_PREFIXES = (
    ("hostnamectl", "status"), ("uname", "-a"),
    ("ip", "-j", "address", "show"), ("ip", "-j", "addr", "show"),
    ("ip", "-j", "link", "show"), ("ip", "-j", "route", "show"),
    ("ip", "-j", "rule", "show"), ("bridge", "-j", "link", "show"),
    ("bridge", "-j", "vlan", "show"),
    ("nft", "-j", "list", "ruleset"), ("nft", "list", "ruleset"),
    ("netplan", "get"), ("systemctl", "show"), ("systemctl", "list-unit-files"),
    ("systemctl", "list-units"), ("dpkg-query", "-W"),
    ("pveversion",), ("qm", "config"), ("qm", "showcmd"), ("pvesh", "get"),
    ("unbound", "-V"), ("pihole-FTL", "--version"), ("pihole-FTL", "-v"),
    ("docker", "inspect"), ("docker", "image", "inspect"), ("docker", "version"),
)


class Refused(RuntimeError):
    """The message is a fixed, content-free identifier."""


def remaining():
    value = END - time.monotonic()
    if value <= 0:
        raise Refused("deadline_exceeded")
    return value


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def fresh(value, maximum=FRESH_SECONDS):
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        age = (dt.datetime.now(dt.timezone.utc) - parsed).total_seconds()
        return parsed.tzinfo is not None and 0 <= age <= maximum
    except (AttributeError, TypeError, ValueError):
        return False


def valid_timestamp(value):
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (parsed.tzinfo is not None and parsed.utcoffset() == dt.timedelta(0)
                and parsed <= dt.datetime.now(dt.timezone.utc))
    except (AttributeError, TypeError, ValueError):
        return False


def strict_path(path):
    path = Path(os.path.abspath(path))
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise Refused("symlink_private_path")
    return path


def private_dir(path):
    path = strict_path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise Refused("unsafe_private_directory")
    return path


def native_work_root():
    # Native exporters require trusted ancestry; /srv may intentionally belong
    # to the operator. Do not chown it or weaken the exporter credential fence.
    if os.geteuid() != 0:
        raise Refused("native_work_requires_root")
    root = strict_path(NATIVE_WORK_ROOT)
    for parent in root.parents:
        if parent.exists():
            value = parent.stat()
            if value.st_uid != 0 or value.st_mode & 0o022:
                raise Refused("unsafe_native_work_ancestor")
    root = private_dir(root)
    if shutil.disk_usage(root).free < MAX_BYTES + RESERVE:
        raise Refused("native_work_space_insufficient")
    return root


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, value):
    strict_path(path)
    fd, name = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        sync_dir(path.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name)


def meta(path):
    value = path.lstat()
    attributes, supported = {}, True
    try:
        for name in sorted(os.listxattr(path, follow_symlinks=False)):
            data = os.getxattr(path, name, follow_symlinks=False)
            if len(data) > MAX_JSON:
                raise Refused("extended_attribute_size_limit")
            attributes[name] = base64.b64encode(data).decode("ascii")
    except OSError as exc:
        if exc.errno not in {errno.ENOTSUP, errno.EOPNOTSUPP}:
            raise Refused("extended_attribute_read_failed") from None
        supported = False
    return {"size": value.st_size, "mtime_ns": value.st_mtime_ns,
            "ctime_ns": value.st_ctime_ns, "inode": value.st_ino, "device": value.st_dev,
            "uid": value.st_uid, "gid": value.st_gid, "mode": stat.S_IMODE(value.st_mode),
            "xattrs_supported": supported, "xattrs_base64": attributes}


def sha(path):
    digest = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise Refused("nonregular_hash_source")
        while data := handle.read(CHUNK):
            remaining()
            digest.update(data)
    return digest.hexdigest()


def private_policy(path):
    path = strict_path(path)
    value = path.stat()
    if (not stat.S_ISREG(value.st_mode) or value.st_uid != 0
            or stat.S_IMODE(value.st_mode) & 0o077 or value.st_size > MAX_JSON):
        raise Refused("unsafe_private_policy")
    policy = json.loads(path.read_text())
    if (policy.get("format") != 1 or set(policy.get("required_sources", [])) != REQUIRED
            or set(policy.get("sources", {})) != REQUIRED):
        raise Refused("incomplete_required_source_policy")
    for name, profile in policy["sources"].items():
        validate_profile(name, profile)
    return policy


def safe_read_command(argv):
    if not isinstance(argv, list) or not argv or any(not isinstance(x, str) or not x or "\x00" in x for x in argv):
        return False
    normalized = [Path(argv[0]).name, *argv[1:]]
    if normalized[0] == "nft":
        return normalized in (["nft", "-j", "list", "ruleset"], ["nft", "list", "ruleset"])
    if normalized[0] == "hostnamectl":
        return normalized in (["hostnamectl", "status"], ["hostnamectl", "status", "--no-pager"])
    if any(tuple(normalized[:len(prefix)]) == prefix for prefix in SAFE_COMMAND_PREFIXES):
        return True
    # Exact revision lookup; no Git hooks, writes, shell aliases or remote access.
    return (len(normalized) == 5 and normalized[:2] == ["git", "-C"]
            and normalized[-2:] == ["rev-parse", "HEAD"] and Path(normalized[2]).is_absolute())


def validate_profile(name, profile):
    expected_kind = "pihole" if name.startswith("pihole-") else name
    if profile.get("kind") != expected_kind or expected_kind not in KINDS:
        raise Refused("invalid_source_kind")
    if profile.get("transport", "ssh") not in {"ssh", "local"}:
        raise Refused("invalid_transport")
    if profile.get("transport") == "local" and expected_kind not in {"unifi", "aruba"}:
        raise Refused("invalid_local_capture_kind")
    if profile.get("transport", "ssh") == "ssh":
        target = profile.get("ssh_target", "")
        if not re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+", target):
            raise Refused("invalid_ssh_target")
        if not profile.get("expected_hostname"):
            raise Refused("source_identity_guard_missing")
    roots = profile.get("roots", [])
    labels = [r.get("label", "") for r in roots]
    if len(set(labels)) != len(labels) or not all(LABEL.fullmatch(label) for label in labels):
        raise Refused("invalid_root_labels")
    if profile.get("approved") is True and not REQUIRED_ROOTS[expected_kind].issubset(labels):
        raise Refused("required_config_roots_missing")
    for root in roots:
        path = PurePosixPath(root.get("path", ""))
        if not path.is_absolute() or ".." in path.parts or str(path) in {"/", "/proc", "/sys", "/dev", "/run"}:
            raise Refused("unsafe_source_root")
    for key in ("volatile_roots", "native_backup_roots", "mongo_data_roots"):
        for excluded_root in profile.get(key, []):
            path = PurePosixPath(excluded_root)
            if not path.is_absolute() or ".." in path.parts or str(path) == "/":
                raise Refused("unsafe_exclusion_root")
            if key == "volatile_roots" and any(Path(root["path"]) == Path(excluded_root)
                                               or Path(excluded_root) in Path(root["path"]).parents
                                               for root in roots if root.get("required", True)):
                raise Refused("volatile_exclusion_hides_required_root")
    commands = profile.get("commands", [])
    names = [c.get("name", "") for c in commands]
    if len(set(names)) != len(names) or not all(LABEL.fullmatch(n) for n in names):
        raise Refused("invalid_command_names")
    if profile.get("approved") is True and not REQUIRED_COMMANDS[expected_kind].issubset(names):
        raise Refused("required_recovery_references_missing")
    if any(not safe_read_command(c.get("argv")) for c in commands):
        raise Refused("unapproved_read_command")
    if expected_kind == "pihole" and profile.get("approved") is True:
        databases = profile.get("sqlite_databases", [])
        if ({x.get("name") for x in databases} != {"gravity", "macvendor"}
                or len(databases) != 2
                or not any(x.get("name") == "gravity" and x.get("required", True) for x in databases)):
            raise Refused("required_gravity_snapshot_missing")
        for value in profile.get("query_history_paths", []):
            if not PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts or value == "/":
                raise Refused("unsafe_query_history_exclusion")
        for database in databases:
            if database.get("name") not in {"gravity", "macvendor"} or Path(database.get("path", "")).name != database["name"] + ".db":
                raise Refused("unapproved_sqlite_database")
    if expected_kind == "unifi" and profile.get("approved") is True:
        images = profile.get("expected_container_images", {})
        if len(images) != 2 or not all(re.fullmatch(r"sha256:[a-f0-9]{64}", image) for image in images.values()):
            raise Refused("unifi_container_image_identity_missing")
        # Excluding live Mongo is a required policy field, never an inferred backup.
        if not profile.get("mongo_data_roots"):
            raise Refused("live_mongo_exclusion_missing")
        for root in profile["mongo_data_roots"]:
            if not PurePosixPath(root).is_absolute() or ".." in PurePosixPath(root).parts:
                raise Refused("unsafe_mongo_exclusion")


def excluded(path, profile):
    name = path.name
    if profile["kind"] == "pihole":
        if name.startswith(("pihole-FTL.db", "pihole-tmp.db")):
            return True  # Never open long-term or forceDisk temporary query history.
        if any(path == Path(value) or str(path).startswith(value + "-")
               for value in profile.get("query_history_paths", [])):
            return True
        for database in profile.get("sqlite_databases", []):
            db = Path(database["path"])
            if path == db or str(path) in {str(db) + "-wal", str(db) + "-shm", str(db) + "-journal"}:
                return True
    if profile["kind"] == "unifi":
        if any(path == Path(root) or Path(root) in path.parents
               for root in profile.get("mongo_data_roots", []) + profile.get("native_backup_roots", [])):
            return True
    if any(path == Path(root) or Path(root) in path.parents for root in profile.get("volatile_roots", [])):
        return True
    return any(fnmatch.fnmatchcase(str(path), pattern) for pattern in profile.get("exclude_globs", []))


def archive_relative(path):
    # Preserve arbitrary POSIX component bytes while producing portable safe tar
    # names. Literal percent is encoded too, so backslash/percent never collide.
    # RECOVERY retains exact original paths; no automatic decoding/extraction.
    return "/".join(quote_from_bytes(os.fsencode(part), safe="-._~@+") for part in path.parts)


def inventory(profile):
    files, links, directories, missing = {}, {}, {}, []
    for root in profile.get("roots", []):
        label, path = root["label"], Path(root["path"])
        # Source links are recorded, never followed. Parent links would redirect scope.
        if any(p.is_symlink() for p in path.parents):
            raise Refused("symlink_source_parent")
        if not path.exists() and not path.is_symlink():
            if root.get("required", True):
                raise Refused("required_config_root_missing")
            missing.append(label)
            continue
        def item(source, name):
            remaining()
            if excluded(source, profile):
                return
            info = source.lstat()
            value = {"source": str(source), **meta(source)}
            if stat.S_ISLNK(info.st_mode):
                links[name] = {**value, "target": os.readlink(source)}
            elif stat.S_ISDIR(info.st_mode):
                directories[name] = value
            elif stat.S_ISREG(info.st_mode):
                files[name] = value
            else:
                raise Refused("nonregular_configuration_file")
            if len(files) + len(links) + len(directories) > MAX_FILES:
                raise Refused("file_count_limit")
        item(path, "files/" + label)
        if path.is_dir() and not path.is_symlink():
            for current, dirs, names in os.walk(path, followlinks=False):
                remaining()
                dirs.sort()
                names.sort()
                for name in list(dirs):
                    source = Path(current) / name
                    archive_name = "files/" + label + "/" + archive_relative(source.relative_to(path))
                    if excluded(source, profile):
                        dirs.remove(name)
                        continue
                    item(source, archive_name)
                    if source.is_symlink():
                        dirs.remove(name)
                for name in names:
                    source = Path(current) / name
                    item(source, "files/" + label + "/" + archive_relative(source.relative_to(path)))
    return {"files": files, "links": links, "directories": directories, "optional_roots_missing": missing}


def stable_inventory(value):
    # Excluded logs/WAL/tmp creation changes directory times without changing
    # selected recovery content. Keep directory identity/permissions, not churn.
    result = dict(value)
    result["directories"] = {name: {k: v for k, v in info.items()
                                     if k not in {"size", "mtime_ns", "ctime_ns"}}
                             for name, info in value["directories"].items()}
    return result


def kill_owned(process):
    if process.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)


def run(argv, *, data=None, output=None, output_limit=MAX_JSON, disk=None):
    with tempfile.TemporaryFile() as captured:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        destination = output if output is not None else captured
        count, error_count = 0, 0
        try:
            if data is not None:
                process.stdin.write(data)
                process.stdin.close()
            streams = [process.stdout, process.stderr]
            while streams:
                ready, _, _ = select.select(streams, [], [], min(remaining(), 1))
                if not ready:
                    continue
                for stream in ready:
                    content = os.read(stream.fileno(), CHUNK)
                    if not content:
                        streams.remove(stream)
                        continue
                    if stream is process.stderr:
                        error_count += len(content)
                        if error_count > CHUNK:
                            raise Refused("command_error_output_limit")
                        continue  # Discard private native stderr; never print it.
                    count += len(content)
                    if count > output_limit:
                        raise Refused("command_output_limit")
                    if disk and shutil.disk_usage(disk).free < RESERVE:
                        raise Refused("disk_reserve_exhausted")
                    destination.write(content)
            if process.wait(timeout=remaining()) != 0:
                raise Refused("capture_command_failed")
            destination.flush()
            if output is not None:
                return b""
            captured.seek(0)
            return captured.read(MAX_JSON + 1)
        finally:
            kill_owned(process)
            process.stdout.close()
            process.stderr.close()
            if process.stdin:
                process.stdin.close()


class Reader:
    def __init__(self, source):
        self.source, self.digest, self.size = source, hashlib.sha256(), 0

    def read(self, size=-1):
        remaining()
        content = self.source.read(size)
        self.size += len(content)
        if self.size > MAX_BYTES:
            raise Refused("archive_size_limit")
        self.digest.update(content)
        return content


class Writer:
    def __init__(self, target):
        self.target, self.size = target, 0

    def write(self, data):
        remaining()
        self.size += len(data)
        if self.size > MAX_BYTES:
            raise Refused("archive_size_limit")
        return self.target.write(data)

    def flush(self):
        self.target.flush()


class Archive:
    def __init__(self, output, kind):
        self.writer = Writer(output)
        self.tar = tarfile.open(fileobj=self.writer, mode="w|", format=tarfile.PAX_FORMAT)
        self.manifest = {"format": 1, "kind": kind, "files": {}}

    def add(self, name, path, expected=None, restore_metadata=None):
        if name in self.manifest["files"] or name == "MANIFEST.json":
            raise Refused("duplicate_archive_member")
        before = meta(path)
        if not stat.S_ISREG(path.lstat().st_mode):
            raise Refused("nonregular_archive_source")
        if expected and any(before[k] != expected[k] for k in before):
            raise Refused("configuration_changed_before_copy")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as handle:
            actual = os.fstat(handle.fileno())
            if (actual.st_ino, actual.st_dev) != (before["inode"], before["device"]):
                raise Refused("configuration_replaced_before_copy")
            reader = Reader(handle)
            info = tarfile.TarInfo(name)
            info.size, info.mode = before["size"], 0o600
            self.tar.addfile(info, reader)
            if reader.size != before["size"] or meta(path) != before:
                raise Refused("configuration_changed_during_copy")
        self.manifest["files"][name] = {**before, "sha256": reader.digest.hexdigest(),
                                         "restore_metadata": restore_metadata or before}

    def import_member(self, name, source, size):
        if name in self.manifest["files"] or name == "MANIFEST.json" or not 0 <= size <= MAX_BYTES:
            raise Refused("invalid_import_member")
        reader = Reader(source)
        info = tarfile.TarInfo(name)
        info.size, info.mode = size, 0o600
        self.tar.addfile(info, reader)
        if reader.size != size:
            raise Refused("truncated_import_member")
        self.manifest["files"][name] = {"size": size, "sha256": reader.digest.hexdigest()}

    def json(self, name, value):
        if name in self.manifest["files"] or name == "MANIFEST.json":
            raise Refused("duplicate_archive_member")
        content = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        if len(content) > MAX_JSON:
            raise Refused("metadata_size_limit")
        info = tarfile.TarInfo(name)
        info.size, info.mode = len(content), 0o600
        self.tar.addfile(info, io.BytesIO(content))
        self.manifest["files"][name] = {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}

    def finish(self):
        data = json.dumps(self.manifest, sort_keys=True, separators=(",", ":")).encode()
        if len(data) > MAX_JSON:
            raise Refused("manifest_size_limit")
        info = tarfile.TarInfo("MANIFEST.json")
        info.size, info.mode = len(data), 0o600
        self.tar.addfile(info, io.BytesIO(data))
        self.tar.close()
        self.writer.flush()


def verify_archive(path, kind):
    found, manifest, recovery = {}, None, None
    with path.open("rb") as handle:
        reader = Reader(handle)
        with tarfile.open(fileobj=reader, mode="r|") as archive:
            for member in archive:
                remaining()
                name, pure = member.name, PurePosixPath(member.name)
                if (not member.isreg() or not pure.parts or pure.is_absolute() or ".." in pure.parts
                        or "\\" in name or name in found or manifest is not None):
                    raise Refused("unsafe_archive_member")
                if member.size < 0 or member.size > MAX_BYTES:
                    raise Refused("archive_size_limit")
                stream = archive.extractfile(member)
                if name == "MANIFEST.json":
                    if member.size > MAX_JSON:
                        raise Refused("manifest_size_limit")
                    manifest = json.loads(stream.read(MAX_JSON + 1))
                    continue
                digest, size = hashlib.sha256(), 0
                recovery_bytes = bytearray() if name == "RECOVERY.json" else None
                while content := stream.read(CHUNK):
                    remaining()
                    digest.update(content)
                    size += len(content)
                    if recovery_bytes is not None:
                        if size > MAX_JSON:
                            raise Refused("metadata_size_limit")
                        recovery_bytes.extend(content)
                if recovery_bytes is not None:
                    recovery = json.loads(recovery_bytes)
                found[name] = {"size": size, "sha256": digest.hexdigest()}
                if len(found) > MAX_FILES:
                    raise Refused("file_count_limit")
        while reader.read(CHUNK):
            pass
    if (not isinstance(manifest, dict) or manifest.get("format") != 1 or manifest.get("kind") != kind
            or set(found) != set(manifest.get("files", {})) or "RECOVERY.json" not in found):
        raise Refused("missing_or_inconsistent_manifest")
    for name, actual in found.items():
        if any(actual[k] != manifest["files"][name].get(k) for k in actual):
            raise Refused("archive_hash_mismatch")
    required = {"pihole": {"databases/gravity.db"}, "unifi": {"native/network-backup"},
                "aruba": {"native/running-config", "native/startup-config", "native/boot-metadata"}}.get(kind, set())
    if not required.issubset(found):
        raise Refused("required_recovery_member_missing")
    if not isinstance(recovery, dict):
        raise Refused("invalid_recovery_metadata")
    complete = kind != "unifi-config"
    native = recovery.get("native", {})
    native_created = None
    if kind == "unifi":
        if (not isinstance(native, dict) or native.get("method") != "native-unifi-backup"
                or native.get("settings_complete") is not True or not valid_timestamp(native.get("native_created_utc"))):
            raise Refused("native_unifi_backup_incomplete")
        native_created = native["native_created_utc"]
    if kind == "aruba":
        if (not isinstance(native, dict) or native.get("method") != "aruba-read-only" or native.get("read_only") is not True
                or native.get("prompt_complete") is not True or native.get("startup_slots_complete") is not True
                or not isinstance(native.get("credentials_complete"), bool)
                or not valid_timestamp(native.get("native_created_utc"))):
            raise Refused("aruba_recovery_scope_incomplete")
        complete, native_created = native["credentials_complete"], native["native_created_utc"]
    return {"sha256": reader.digest.hexdigest(), "bytes": reader.size, "members": len(found),
            "coverage_complete": complete, "native_created_utc": native_created}


def sqlite_snapshot(source, destination):
    source = strict_path(source)
    restore = meta(source)
    if not stat.S_ISREG(source.lstat().st_mode) or source.stat().st_size > MAX_BYTES:
        raise Refused("unsafe_sqlite_source")
    if shutil.disk_usage(destination.parent).free < RESERVE + 2 * source.stat().st_size:
        raise Refused("sqlite_snapshot_space_insufficient")
    with contextlib.closing(sqlite3.connect("file:" + str(source) + "?mode=ro", uri=True, timeout=5)) as original:
        original.execute("PRAGMA query_only=ON")
        original.execute("BEGIN")
        original.execute("SELECT count(*) FROM sqlite_master").fetchone()
        with contextlib.closing(sqlite3.connect(destination)) as snapshot:
            original.backup(snapshot, pages=256, progress=lambda *_: remaining(), sleep=0.01)
            if snapshot.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise Refused("sqlite_snapshot_integrity_failed")
            snapshot.commit()
        original.rollback()
    return restore


def native_export(profile, temporary):
    native = profile.get("native_export", {})
    if native.get("approved") is not True:
        raise Refused("native_export_method_not_approved")
    executable = strict_path(native.get("executable", "/nonexistent"))
    value = executable.stat()
    if any(p.stat().st_uid != 0 or p.stat().st_mode & 0o022 for p in executable.parents):
        raise Refused("unsafe_native_exporter_parent")
    if (not stat.S_ISREG(value.st_mode) or value.st_uid != 0 or value.st_mode & 0o022
            or not value.st_mode & 0o100 or sha(executable) != native.get("sha256")):
        raise Refused("native_exporter_identity_mismatch")
    output = private_dir(temporary / "native")
    response = json.loads(run([str(executable), "--output-directory", str(output)]))
    if not fresh(response.get("native_created_utc")):
        raise Refused("native_backup_stale_or_invalid")
    required = {"unifi": {"network-backup"},
                "aruba": {"running-config", "startup-config", "boot-metadata"}}[profile["kind"]]
    artifacts = response.get("artifacts", {})
    if not required.issubset(artifacts):
        raise Refused("native_backup_artifact_missing")
    if profile["kind"] == "unifi":
        if response.get("method") != "native-unifi-backup" or response.get("settings_complete") is not True:
            raise Refused("native_unifi_backup_incomplete")
    else:
        if (response.get("method") != "aruba-read-only" or response.get("read_only") is not True
                or response.get("prompt_complete") is not True or response.get("startup_slots_complete") is not True
                or not isinstance(response.get("credentials_complete"), bool)):
            raise Refused("aruba_recovery_scope_incomplete")
    result = {}
    for label, name in artifacts.items():
        if not LABEL.fullmatch(label):
            raise Refused("invalid_native_artifact_label")
        path = strict_path(name)
        if output not in path.parents or not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
            raise Refused("unsafe_native_artifact")
        result[label] = path
    return response, result


def stable_reference(name, value):
    if name in {"controller-inspect", "database-inspect"}:
        rows = json.loads(value)
        return [{"Id": row.get("Id"), "Name": row.get("Name"), "Image": row.get("Image"),
                 "State": {k: (row.get("State") or {}).get(k) for k in ("Running", "StartedAt", "Paused", "Restarting", "Dead")},
                 "Config": row.get("Config"),
                 "Mounts": sorted(row.get("Mounts", []), key=lambda m: (m.get("Destination", ""), m.get("Source", "")))}
                for row in sorted(rows, key=lambda r: r.get("Name", ""))]
    if name == "software-images":
        return [{k: row.get(k) for k in ("Id", "Os", "Architecture", "Created")}
                | {"RepoDigests": sorted(row.get("RepoDigests") or [])}
                for row in sorted(json.loads(value), key=lambda r: r.get("Id", ""))]
    return value


def unifi_identity(profile, references):
    expected = profile["expected_container_images"]
    observed, containers = {}, {}
    for name in ("controller-inspect", "database-inspect"):
        rows = json.loads(references[name]["output"])
        if len(rows) != 1:
            raise Refused("unifi_container_identity_changed")
        row = rows[0]
        if (row.get("State") or {}).get("Running") is not True:
            raise Refused("unifi_required_container_not_running")
        observed[row.get("Name", "").lstrip("/")] = row.get("Image")
        containers[name] = row
    if observed != expected:
        raise Refused("unifi_container_identity_changed")
    database_mounts = [mount for mount in containers["database-inspect"].get("Mounts", [])
                       if mount.get("Destination") in {"/data/db", "/data/configdb"}]
    if (not any(mount.get("Destination") == "/data/db" for mount in database_mounts)
            or any(mount.get("Source") not in profile["mongo_data_roots"] for mount in database_mounts)):
        raise Refused("live_mongo_mount_not_excluded")
    configuration_mounts = [mount for mount in containers["controller-inspect"].get("Mounts", [])
                            if mount.get("Destination") == "/config"]
    if len(configuration_mounts) != 1:
        raise Refused("controller_configuration_mount_missing")
    source = strict_path(configuration_mounts[0]["Source"])
    if not any(source == Path(root["path"]) or Path(root["path"]) in source.parents
               for root in profile["roots"]):
        raise Refused("controller_configuration_mount_not_selected")


def configuration_checkpoint(profile):
    if (profile.get("approved") is not True or profile["kind"] != "unifi"
            or socket.gethostname() != profile.get("expected_hostname")):
        raise Refused("configuration_checkpoint_identity_mismatch")
    before = inventory(profile)
    files = {}
    for name, expected in sorted(before["files"].items()):
        source = Path(expected["source"])
        actual = meta(source)
        if any(actual[key] != expected[key] for key in actual):
            raise Refused("configuration_changed_during_checkpoint")
        files[name] = sha(source)
        if meta(source) != actual:
            raise Refused("configuration_changed_during_checkpoint")
    references = {}
    for command in profile["commands"]:
        if command.get("stable") is True or command["name"] in REQUIRED_COMMANDS["unifi"]:
            references[command["name"]] = {"output": run(command["argv"]).decode("utf-8")}
    unifi_identity(profile, references)
    if stable_inventory(inventory(profile)) != stable_inventory(before):
        raise Refused("configuration_changed_during_checkpoint")
    value = {"inventory": stable_inventory(before), "file_sha256": files,
             "references": {name: stable_reference(name, item["output"]) for name, item in references.items()}}
    digest = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"format": 1, "kind": "unifi-configuration-checkpoint", "sha256": digest, "files": len(files)}


def worker(profile, output, temporary_root=None):
    if profile.get("approved") is not True:
        raise Refused("source_method_not_approved")
    if profile.get("expected_hostname") and socket.gethostname() != profile["expected_hostname"]:
        raise Refused("source_host_identity_mismatch")
    deferred = profile["kind"] == "unifi" and profile.get("_hub_native_pending") is True
    if deferred and profile.get("native_export", {}).get("location") != "hub":
        raise Refused("invalid_deferred_native_capture")
    if temporary_root is None and profile["kind"] in {"unifi", "aruba"} and not deferred:
        temporary_root = native_work_root()
    before = inventory(profile)
    if sum(value["size"] for value in before["files"].values()) > MAX_BYTES:
        raise Refused("source_size_limit")
    with tempfile.TemporaryDirectory(prefix="edsys-network-export-", dir=temporary_root) as directory:
        temporary = private_dir(Path(directory))
        databases = []
        for database in profile.get("sqlite_databases", []):
            source = Path(database["path"])
            if not source.exists():
                if database.get("required", True):
                    raise Refused("required_sqlite_database_missing")
                continue
            destination = temporary / (database["name"] + ".db")
            restore = sqlite_snapshot(source, destination)
            databases.append((database["name"], source, destination, restore))
        references = {}
        for command in profile.get("commands", []):
            references[command["name"]] = {"captured_utc": utc(), "output": run(command["argv"]).decode("utf-8")}
        if profile["kind"] == "unifi":
            unifi_identity(profile, references)
        native, artifacts = ({}, {})
        if profile["kind"] in {"unifi", "aruba"} and not deferred:
            native, artifacts = native_export(profile, temporary)
        archive = Archive(output, "unifi-config" if deferred else profile["kind"])
        try:
            for name, value in sorted(before["files"].items()):
                archive.add(name, Path(value["source"]), value)
            for name, source, snapshot, restore in databases:
                archive.add("databases/" + name + ".db", snapshot, restore_metadata=restore)
            for label, path in sorted(artifacts.items()):
                archive.add("native/" + label, path)
            if stable_inventory(inventory(profile)) != stable_inventory(before):
                raise Refused("configuration_changed_during_capture")
            # Container configuration and immutable software references must not
            # change around the native capture. Volatile network counters/routes
            # remain timestamped read references, never an instruction to replay.
            for command in profile.get("commands", []):
                name = command["name"]
                if command.get("stable") is True or name in {"controller-inspect", "database-inspect", "software-images"}:
                    after = run(command["argv"]).decode("utf-8")
                    if stable_reference(name, after) != stable_reference(name, references[name]["output"]):
                        raise Refused("software_or_container_configuration_changed")
            archive.json("RECOVERY.json", {"format": 1, "kind": profile["kind"], "captured_utc": utc(),
                         "hostname": socket.gethostname(), "inventory": before, "collection_policy": profile,
                         "references": references, "sqlite_sources": [str(x[1]) for x in databases],
                         "native": native, "symlink_restore": "metadata only; never follow/extract links automatically",
                         "network_references": "timestamped observations; never replay automatically",
                         "live_mongo_included": False, "running_restore_verified": False})
            archive.finish()
        finally:
            # Close the stream while its owned output is still open, including a
            # failed source-change guard. No destructor traceback or payload leak.
            with contextlib.suppress(BaseException):
                archive.tar.close()


def ssh_capture(args, profile, output=None, checkpoint=False):
    # The policy (including private paths) travels on protected SSH stdin, never
    # in argv or ordinary logs. Only this reviewed source executes remotely.
    source = Path(__file__).read_text()
    envelope = "\nREMOTE_PROFILE = " + repr(profile) + "\n"
    prefix, suffix = source.rsplit('\nif __name__ == "__main__":', 1)
    source = prefix + envelope + '\nif __name__ == "__main__":' + suffix
    action = "_checkpoint" if checkpoint else "_remote"
    remote = "sudo -n timeout --signal=TERM --kill-after=5s 840s nice -n 15 ionice -c 2 -n 7 python3 - " + action
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=yes",
            "-o", "UserKnownHostsFile=" + args.known_hosts, "-o", "IdentitiesOnly=yes", "-i", args.ssh_key,
            profile["ssh_target"], remote]
    return run(argv, data=source.encode(), output=output,
               output_limit=MAX_JSON if checkpoint else MAX_BYTES, disk=Path(args.stage_root))


def merge_hub_native(configuration, output, native, artifacts):
    verify_archive(configuration, "unifi-config")
    archive = Archive(output, "unifi")
    recovery, recorded = None, None
    try:
        with tarfile.open(configuration, "r|") as source:
            for member in source:
                stream = source.extractfile(member)
                if member.name == "MANIFEST.json":
                    recorded = json.loads(stream.read(MAX_JSON + 1))
                elif member.name == "RECOVERY.json":
                    if member.size > MAX_JSON:
                        raise Refused("metadata_size_limit")
                    recovery = json.loads(stream.read(MAX_JSON + 1))
                else:
                    archive.import_member(member.name, stream, member.size)
        if not isinstance(recovery, dict) or not isinstance(recorded, dict):
            raise Refused("configuration_checkpoint_missing")
        for name, actual in archive.manifest["files"].items():
            expected = recorded["files"][name]
            if any(actual[key] != expected.get(key) for key in ("size", "sha256")):
                raise Refused("configuration_import_hash_mismatch")
            archive.manifest["files"][name] = expected
        for label, path in sorted(artifacts.items()):
            archive.add("native/" + label, path)
        recovery["native"] = native
        recovery["native_collected_on_hub_utc"] = utc()
        archive.json("RECOVERY.json", recovery)
        archive.finish()
    finally:
        with contextlib.suppress(BaseException):
            archive.tar.close()


def read_current(root, filename="CURRENT.json"):
    if filename not in {"CURRENT.json", "LIMITED.json"}:
        raise Refused("invalid_pointer_name")
    path = strict_path(root / filename)
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    if (not isinstance(value, dict) or not GENERATION.fullmatch(value.get("current", ""))
            or (value.get("previous") and not GENERATION.fullmatch(value["previous"]))):
        raise Refused("invalid_current_pointer")
    return value


def publish(root, pending, summary, limited=False):
    pointer_name = "LIMITED.json" if limited else "CURRENT.json"
    other_name = "CURRENT.json" if limited else "LIMITED.json"
    old, other = read_current(root, pointer_name), read_current(root, other_name)
    protected = {old.get("current"), other.get("current"), other.get("previous")}
    generations = private_dir(root / "generations")
    cleanup = []
    for candidate in generations.iterdir():
        if candidate.name in protected or not GENERATION.fullmatch(candidate.name):
            continue
        strict_path(candidate)
        value = candidate.stat()
        if not candidate.is_dir() or value.st_uid != os.geteuid() or stat.S_IMODE(value.st_mode) & 0o077:
            raise Refused("unsafe_old_staging_generation")
        cleanup.append(candidate)
    atomic_json(pending / "generation.json", summary)
    destination = generations / pending.name
    os.rename(pending, destination)
    sync_dir(generations)
    atomic_json(root / pointer_name, {"current": destination.name, "previous": old.get("current"),
                                   "completed_utc": summary["completed_utc"]})
    # A limited export never replaces/removes complete CURRENT/previous data.
    # Retention applies only to this helper's owned staging generations.
    for candidate in cleanup:
        with contextlib.suppress(OSError):
            shutil.rmtree(candidate)


def stage_source(args, name, profile):
    root = private_dir(Path(args.stage_root) / "sources" / name)
    old = {}
    with strict_path(root / ".lock").open("a") as lock:
        os.chmod(root / ".lock", 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused("source_already_running") from None
        pending = None
        try:
            old = read_current(root)
            if profile.get("approved") is not True:
                raise Refused("source_method_not_approved")
            if shutil.disk_usage(root).free < 3 * MAX_BYTES + RESERVE:
                raise Refused("space_for_three_stages_and_reserve_insufficient")
            incoming = private_dir(Path(args.stage_root) / ".incoming" / name)
            generation = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
            pending = private_dir(incoming / generation)
            with (pending / "network.tar").open("xb") as handle:
                if profile["kind"] == "unifi" and profile.get("native_export", {}).get("location") == "hub":
                    if profile.get("transport", "ssh") != "ssh":
                        raise Refused("invalid_hub_native_transport")
                    with tempfile.TemporaryDirectory(prefix="edsys-unifi-hub-", dir=native_work_root()) as temporary:
                        first = json.loads(ssh_capture(args, profile, checkpoint=True))
                        native, artifacts = native_export(profile, Path(temporary))
                        configuration = pending / "configuration.tar"
                        deferred = {**profile, "_hub_native_pending": True}
                        with configuration.open("xb") as remote_output:
                            ssh_capture(args, deferred, remote_output)
                        last = json.loads(ssh_capture(args, profile, checkpoint=True))
                        if first != last or first.get("kind") != "unifi-configuration-checkpoint":
                            raise Refused("native_configuration_checkpoint_changed")
                        native["configuration_checkpoint"] = first
                        merge_hub_native(configuration, handle, native, artifacts)
                        configuration.unlink()
                elif profile.get("transport", "ssh") == "local":
                    worker(profile, handle, temporary_root=native_work_root())
                else:
                    ssh_capture(args, profile, handle)
                handle.flush()
                os.fsync(handle.fileno())
            proof = verify_archive(pending / "network.tar", profile["kind"])
            summary = {"format": 1, "kind": profile["kind"], "completed_utc": utc(), "archive": proof,
                       "running_restore_verified": False, "offsite_verified": False}
            limited = not proof["coverage_complete"]
            publish(root, pending, summary, limited=limited)
            pending = None
            health = {"status": "limited" if limited else "healthy",
                      "reason": "switch_credentials_or_keys_not_exported" if limited else "verified_export",
                      "last_success_utc": old.get("completed_utc") if limited else summary["completed_utc"],
                      "last_export_utc": summary["completed_utc"], "attempted_utc": utc(),
                      "members": proof["members"], "coverage_complete": not limited,
                      "native_created_utc": proof["native_created_utc"], "running_restore_verified": False}
            atomic_json(root / "status.json", health)
            return health
        except BaseException as exc:
            reason = str(exc) if isinstance(exc, Refused) else type(exc).__name__
            health = {"status": "failed", "reason": reason, "last_success_utc": old.get("completed_utc"), "attempted_utc": utc()}
            atomic_json(root / "status.json", health)
            raise
        finally:
            if pending is not None and pending.exists():
                shutil.rmtree(pending)


def status(args, honor_attempt=True):
    result = {}
    for name in sorted(REQUIRED):
        try:
            path = strict_path(Path(args.stage_root) / "sources" / name / "status.json")
            health = json.loads(path.read_text())
            if not isinstance(health, dict):
                raise Refused("invalid_status_record")
            state = health.get("status", "invalid")
            native_fresh = health.get("native_created_utc") is None or fresh(health.get("native_created_utc"))
            if state == "healthy" and (not fresh(health.get("last_success_utc")) or not native_fresh):
                state = "stale"
            if state == "limited" and (not fresh(health.get("last_export_utc")) or not native_fresh):
                state = "limited_stale"
            if state in {"healthy", "limited"}:
                root = Path(args.stage_root) / "sources" / name
                pointer = read_current(root, "LIMITED.json" if state == "limited" else "CURRENT.json")
                if not pointer:
                    raise Refused("published_generation_missing")
                directory = strict_path(root / "generations" / pointer["current"])
                summary = json.loads(strict_path(directory / "generation.json").read_text())
                archive = strict_path(directory / "network.tar")
                timestamp = health.get("last_export_utc") if state == "limited" else health.get("last_success_utc")
                expected_kind = "pihole" if name.startswith("pihole-") else name
                if (not archive.is_file() or not stat.S_ISREG(archive.lstat().st_mode)
                        or archive.stat().st_size != summary["archive"]["bytes"]
                        or summary.get("kind") != expected_kind or summary.get("completed_utc") != timestamp
                        or pointer.get("completed_utc") != timestamp
                        or summary["archive"].get("coverage_complete") != (state == "healthy")
                        or summary["archive"].get("native_created_utc") != health.get("native_created_utc")):
                    raise Refused("published_generation_invalid")
            result[name] = {"status": state, "last_success_utc": health.get("last_success_utc"),
                            "last_export_utc": health.get("last_export_utc"),
                            "native_created_utc": health.get("native_created_utc"), "reason": health.get("reason", "invalid")}
        except (OSError, ValueError, TypeError, KeyError, AttributeError, Refused):
            result[name] = {"status": "missing", "last_success_utc": None, "reason": "no_valid_status"}
    healthy = sum(x["status"] == "healthy" for x in result.values())
    overall = "healthy" if healthy == len(REQUIRED) else "degraded"
    last_attempt = None
    if honor_attempt:
        try:
            previous = json.loads(strict_path(Path(args.stage_root) / "status.json").read_text())
            last_attempt = previous.get("last_attempt_status")
            if last_attempt == "failed":
                overall = "failed"
        except (OSError, ValueError, TypeError, AttributeError, Refused):
            pass
    return {"status": overall, "healthy_sources": healthy, "required_sources": len(REQUIRED),
            "sources": result, "last_attempt_status": last_attempt, "offsite_verified": False}


def verify_source(root, kind):
    outcomes = {}
    for pointer_name in ("CURRENT.json", "LIMITED.json"):
        current = read_current(root, pointer_name)
        if not current:
            continue
        directory = strict_path(root / "generations" / current["current"])
        summary = json.loads(strict_path(directory / "generation.json").read_text())
        proof = verify_archive(strict_path(directory / "network.tar"), kind)
        if summary.get("kind") != kind or proof != summary.get("archive"):
            raise Refused("generation_manifest_mismatch")
        outcomes[pointer_name] = {"members": proof["members"], "completed_utc": summary["completed_utc"],
                                  "coverage_complete": proof["coverage_complete"]}
    if not outcomes:
        raise Refused("no_verified_generation")
    try:
        preferred = max(outcomes.values(), key=lambda item: dt.datetime.fromisoformat(item["completed_utc"].replace("Z", "+00:00")))
    except (TypeError, ValueError):
        raise Refused("invalid_generation_time") from None
    return {"status": "verified" if preferred["coverage_complete"] else "verified_limited", **preferred,
            "pointers": outcomes}


def main():
    global END
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stage", "verify", "status", "_remote", "_checkpoint"))
    parser.add_argument("--config", default="/etc/edsys-backup/network-recovery.json")
    parser.add_argument("--stage-root", default="/srv/edsys-backup/staging/network-recovery")
    parser.add_argument("--source", choices=sorted(REQUIRED))
    parser.add_argument("--ssh-key", default="/home/jeremy/.ssh/id_ed25519")
    parser.add_argument("--known-hosts", default="/etc/edsys-backup/known_hosts")
    parser.add_argument("--deadline-seconds", type=int, default=900)
    args = parser.parse_args()
    if not 1 <= args.deadline_seconds <= 900:
        parser.error("deadline must be between 1 and 900 seconds")
    END = time.monotonic() + args.deadline_seconds
    def interrupt(_signum, _frame):
        raise Refused("deadline_or_interrupt")
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM):
        signal.signal(signum, interrupt)
    signal.setitimer(signal.ITIMER_REAL, args.deadline_seconds)
    try:
        if args.action in {"_remote", "_checkpoint"}:
            profile = globals().get("REMOTE_PROFILE")
            if not isinstance(profile, dict):
                raise Refused("remote_profile_missing")
            if args.action == "_checkpoint":
                print(json.dumps(configuration_checkpoint(profile), sort_keys=True))
            else:
                worker(profile, sys.stdout.buffer)
            return 0
        if args.action == "status":
            result = status(args)
        else:
            policy = private_policy(Path(args.config))
            selected = [args.source] if args.source else sorted(REQUIRED)
            outcomes = {}
            for name in selected:
                try:
                    if args.action == "stage":
                        outcomes[name] = stage_source(args, name, policy["sources"][name])
                    else:
                        outcomes[name] = verify_source(Path(args.stage_root) / "sources" / name, policy["sources"][name]["kind"])
                except Exception as exc:
                    outcomes[name] = {"status": "failed", "reason": str(exc) if isinstance(exc, Refused) else type(exc).__name__}
            result = {"status": "healthy" if all(x["status"] in {"healthy", "verified"} for x in outcomes.values()) else "degraded",
                      "selected_sources": len(selected), "outcomes": outcomes, "coverage": status(args, honor_attempt=False)}
            if args.action == "stage":
                record = {**result["coverage"], "last_attempt_status": result["status"], "attempted_utc": utc()}
                atomic_json(private_dir(Path(args.stage_root)) / "status.json", record)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == "healthy" else 1
    except BaseException as exc:
        reason = str(exc) if isinstance(exc, Refused) else type(exc).__name__
        if args.action == "stage":
            with contextlib.suppress(BaseException):
                atomic_json(private_dir(Path(args.stage_root)) / "status.json",
                            {"status": "failed", "last_attempt_status": "failed", "reason": reason, "attempted_utc": utc()})
        print(json.dumps({"status": "failed", "reason": reason}), file=sys.stderr)
        return 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == "__main__":
    raise SystemExit(main())
