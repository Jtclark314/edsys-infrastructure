#!/usr/bin/python3
"""Consistent, narrowly scoped recovery for the two private web applications.

Private state, manifests and receipts stay outside Git. Commands print aggregate
results only; subprocess output and application configuration are never logged.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import uuid
import yaml


SCOPE = "edsys-private-web-apps"
FORMAT_TAG = "edsys-private-web-apps-v1"
APPS = {
    "stirling-pdf": "edsys-stirling-pdf",
    "changedetection": "edsys-changedetection",
}
STATE_BINDS = {
    "stirling-pdf": {"configs": "/configs", "signatures": "/customFiles/signatures",
                     "templates": "/customFiles/templates", "pipeline": "/pipeline"},
    "changedetection": {"": "/datastore"},
}
PAYLOAD_FILES = {"manifest.json", "stirling-pdf.tar", "changedetection.tar", "recovery.tar"}
MAX_STATE_BYTES = 32 * 1024**3
MAX_IMAGE_BYTES = 40 * 1024**3
MAX_FILES = 100_000
CAPTURE_SECONDS = 180
SPACE_RESERVE_BYTES = 2 * 1024**3
GENERATION_PATTERN = r"[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}"
CONFIG_KEYS = (
    "RESTIC_REPOSITORY", "RESTIC_PASSWORD_FILE", "RESTIC_CACHE_DIR",
    "RESTIC_LOCK_RETRY", "RCLONE_CONFIG", "RCLONE_OFFSITE_DEST", "RCLONE_BIN",
)


class BackupError(RuntimeError):
    """A sanitized failure code, never raw application/command output."""


@dataclasses.dataclass(frozen=True)
class Paths:
    runtime: Path = Path("/mnt/ai-store/edsys-private-services")
    stage: Path = Path("/srv/edsys-backup/staging/private-web-apps")
    source: Path = Path("/srv/edsys/edsys-infrastructure")
    system_units: Path = Path("/etc/systemd/system")
    user_units: Path = Path("/home/jeremy/.config/systemd/user")
    libexec: Path = Path("/usr/local/libexec")
    mount_guard: Path = Path("/etc/edsys-private-web-apps/mount-uuid")
    lock: Path = Path("/run/edsys-backup.lock")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        with temp.open("x", encoding="utf-8") as output:
            os.chmod(temp, 0o600)
            json.dump(data, output, sort_keys=True, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temp.unlink(missing_ok=True)


def run(command: list[str], *, label: str, timeout: int = 120,
        env: dict | None = None) -> bytes:
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    except OSError:
        raise BackupError(label + "_unavailable") from None
    try:
        output, _ = process.communicate(timeout=timeout)
    except BaseException as error:
        # Give Restic and other clients a bounded chance to remove their locks.
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            process.terminate()
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
        if isinstance(error, subprocess.TimeoutExpired):
            raise BackupError(label + "_timeout") from None
        raise
    if process.returncode:
        raise BackupError(label + "_failed")
    return output


@contextlib.contextmanager
def defer_stop_termination():
    """Let a bounded Docker stop finish before processing systemd cancellation."""
    pending = False
    def mark_pending(_signal, _frame):
        nonlocal pending
        pending = True
    previous = signal.signal(signal.SIGTERM, mark_pending)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)
        if pending:
            raise BackupError("terminated")


@contextlib.contextmanager
def bounded(seconds: int):
    def expired(_signal, _frame):
        raise BackupError("capture_timeout")
    previous = signal.signal(signal.SIGALRM, expired)
    old_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *old_timer)
        signal.signal(signal.SIGALRM, previous)


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_record(path: Path, relative: str) -> dict:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size == 0:
        raise BackupError("missing_or_empty_required_file")
    digest = hash_file(path)
    after = path.lstat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino, after.st_size, after.st_mtime_ns):
        raise BackupError("source_changed_during_capture")
    return {"path": relative, "size": before.st_size, "sha256": digest,
            "mode": stat.S_IMODE(before.st_mode), "uid": before.st_uid, "gid": before.st_gid}


def plain_tree(root: Path, *, max_bytes: int) -> list[Path]:
    if not root.is_dir() or root.is_symlink():
        raise BackupError("required_directory_missing")
    files = []
    total = 0
    for base, dirs, names in os.walk(root, followlinks=False):
        for name in dirs + names:
            path = Path(base) / name
            mode = path.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise BackupError("unsupported_source_file_type")
            if stat.S_ISREG(mode):
                files.append(path)
                total += path.stat().st_size
                if len(files) > MAX_FILES or total > max_bytes:
                    raise BackupError("capture_size_limit")
    if not files or not total:
        raise BackupError("empty_required_directory")
    return sorted(files)


def estimate_archive_bytes(sources: list[tuple[Path, str]]) -> int:
    """Conservative uncompressed tar/manifest allowance, including long paths."""
    data_bytes = 0
    entries = 0
    for source, _ in sources:
        def members():
            yield source
            if source.is_dir():
                for base, dirs, names in os.walk(source, followlinks=False):
                    for name in dirs + names:
                        yield Path(base) / name
        for path in members():
            info = path.lstat()
            if stat.S_ISREG(info.st_mode):
                data_bytes += info.st_size
            elif not stat.S_ISDIR(info.st_mode):
                raise BackupError("unsupported_source_file_type")
            entries += 1
            if data_bytes > MAX_STATE_BYTES or entries > MAX_FILES:
                raise BackupError("capture_size_limit")
    return data_bytes + entries * 8192 + 10240


def preflight_space(paths: Paths, sources: list[tuple[Path, str]], images: list[dict],
                    repository_path: Path | None) -> dict:
    archive_bytes = estimate_archive_bytes(sources)
    for app in APPS:
        plain_tree(paths.runtime / app, max_bytes=MAX_STATE_BYTES)
        archive_bytes += estimate_archive_bytes([(paths.runtime / app, ".")])
    image_bytes = sum(image["size"] for image in images)
    stage_required = archive_bytes + SPACE_RESERVE_BYTES
    if repository_path is not None:
        if not repository_path.is_absolute() or not repository_path.is_dir():
            raise BackupError("local_repository_missing")
        # Do not assume deduplication will make this capture fit. Reserve enough
        # for a full new encrypted copy plus retained image exports.
        repository_required = archive_bytes + image_bytes + SPACE_RESERVE_BYTES
        if paths.stage.stat().st_dev == repository_path.stat().st_dev:
            stage_required += archive_bytes + image_bytes
        elif shutil.disk_usage(repository_path).free < repository_required:
            raise BackupError("repository_space_insufficient")
    if shutil.disk_usage(paths.stage).free < stage_required:
        raise BackupError("staging_space_insufficient")
    return {"estimated_archive_bytes": archive_bytes, "stage_required_free_bytes": stage_required,
            "reserve_bytes": SPACE_RESERVE_BYTES}


def make_archive(target: Path, sources: list[tuple[Path, str]], *, seconds: int) -> None:
    with bounded(seconds):
        with tarfile.open(target, "w", format=tarfile.PAX_FORMAT, dereference=False) as archive:
            for source, name in sources:
                archive.add(source, arcname=name, recursive=True)
    os.chmod(target, 0o600)


def archive_inventory(path: Path, *, app: str | None = None) -> list[dict]:
    records = []
    seen = set()
    total = 0
    changedetection = None
    stirling_settings = False
    try:
        with tarfile.open(path, "r:") as archive:
            for member in archive:
                relative = PurePosixPath(member.name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise BackupError("unsafe_archive_path")
                name = str(relative)
                if len(records) >= MAX_FILES or name in seen or not (member.isfile() or member.isdir()):
                    raise BackupError("unsupported_archive_member")
                seen.add(name)
                item = {"path": name, "mode": member.mode, "uid": member.uid,
                        "gid": member.gid, "type": "file" if member.isfile() else "directory"}
                if member.isfile():
                    total += member.size
                    if total > MAX_STATE_BYTES or len(records) > MAX_FILES:
                        raise BackupError("archive_size_limit")
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise BackupError("unreadable_archive_member")
                    digest = hashlib.sha256()
                    special = bytearray()
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                        if ((app == "changedetection" and name == "changedetection.json")
                                or (app == "stirling-pdf" and name in ("configs/settings.yml", "configs/custom_settings.yml"))):
                            special.extend(block)
                            if len(special) > 64 * 1024**2:
                                raise BackupError("settings_size_limit")
                    item.update(size=member.size, sha256=digest.hexdigest())
                    if app == "changedetection" and name == "changedetection.json":
                        changedetection = json.loads(special)
                    if app == "stirling-pdf" and name in ("configs/settings.yml", "configs/custom_settings.yml"):
                        try:
                            settings = yaml.safe_load(bytes(special))
                        except yaml.YAMLError:
                            raise BackupError("stirling_settings_invalid") from None
                        if name == "configs/settings.yml":
                            if not member.size or not isinstance(settings, dict):
                                raise BackupError("stirling_settings_invalid")
                            stirling_settings = True
                        elif settings is not None and not isinstance(settings, dict):
                            raise BackupError("stirling_custom_settings_invalid")
                records.append(item)
    except (tarfile.TarError, OSError, ValueError, UnicodeError):
        raise BackupError("invalid_archive") from None
    if not total:
        raise BackupError("empty_archive")
    if app == "stirling-pdf":
        if not stirling_settings:
            raise BackupError("stirling_settings_missing")
        directories = {item["path"] for item in records if item["type"] == "directory"}
        if not set(STATE_BINDS[app]).issubset(directories):
            raise BackupError("stirling_persistent_directory_missing")
    if app == "changedetection":
        # 0.60.10 serializes settings here and watches in {uuid}/watch.json;
        # an empty production watch list therefore has no top-level watching.
        if not isinstance(changedetection, dict):
            raise BackupError("changedetection_store_invalid")
        settings_root = changedetection.get("settings")
        settings = settings_root.get("application") if isinstance(settings_root, dict) else None
        if not isinstance(settings, dict):
            raise BackupError("changedetection_store_invalid")
        token = settings.get("api_access_token")
        if settings.get("api_access_token_enabled") is not True or not isinstance(token, str) or not token.strip():
            raise BackupError("changedetection_private_api_state_missing")
    return records


def docker_inspect(identifier: str, app: str, paths: Paths) -> dict:
    output = run(["docker", "--host", "unix:///var/run/docker.sock", "inspect", identifier],
                 label="container_inspect", timeout=15)
    try:
        info, = json.loads(output)
        expected = {(str((paths.runtime / app / subdir).resolve()), destination, True)
                    for subdir, destination in STATE_BINDS[app].items()}
        if app == "changedetection":
            expected.update((str((paths.source / "docker/changedetection" / name).resolve()),
                             "/opt/edsys/" + name, False) for name in ("initialize.py", "healthcheck.py"))
        actual = {(m.get("Source"), m.get("Destination"), m.get("RW"))
                  for m in info["Mounts"] if m.get("Type") == "bind"}
        unknown_mount = any(m.get("Type") not in ("bind", "tmpfs") for m in info["Mounts"])
        if (info["Name"] != "/" + APPS[app] or actual != expected or unknown_mount
                or info["State"].get("Paused")):
            raise BackupError("container_identity_mismatch")
        if info["State"].get("Restarting"):
            raise BackupError("container_restarting")
        return info
    except (KeyError, TypeError, ValueError):
        raise BackupError("container_inspect_invalid") from None


def recover_running(paths: Paths) -> bool:
    intent = paths.stage / "restart-intent.json"
    if not intent.exists():
        return False
    try:
        data = json.loads(intent.read_text())
        app = data["app"]
        identifier = data["container_id"]
        if app not in APPS or not re.fullmatch(r"[a-f0-9]{64}", identifier):
            raise BackupError("restart_intent_invalid")
        current = docker_inspect(identifier, app, paths)
        if not current["State"]["Running"]:
            created = dt.datetime.fromisoformat(data["created_at"])
            age = (dt.datetime.now(dt.timezone.utc) - created).total_seconds()
            if not 0 <= age <= 900:
                raise BackupError("stale_restart_intent_requires_review")
            run([str(paths.libexec / "edsys-private-web-apps-mount-check")],
                label="mount_guard", timeout=15)
            run(["docker", "--host", "unix:///var/run/docker.sock", "start", identifier],
                label="container_restart", timeout=45)
        if not docker_inspect(identifier, app, paths)["State"]["Running"]:
            raise BackupError("container_restart_unconfirmed")
        intent.unlink()
        return True
    except (KeyError, ValueError, OSError):
        raise BackupError("restart_recovery_failed") from None


def capture_app(app: str, output: Path, paths: Paths) -> dict:
    root = paths.runtime / app
    plain_tree(root, max_bytes=MAX_STATE_BYTES)
    info = docker_inspect(APPS[app], app, paths)
    running = bool(info["State"]["Running"])
    if running:
        atomic_json(paths.stage / "restart-intent.json",
                    {"app": app, "container_id": info["Id"], "created_at": utc_now()})
    try:
        if running:
            with defer_stop_termination():
                run(["docker", "--host", "unix:///var/run/docker.sock", "stop", "--time", "45", info["Id"]],
                    label="container_stop", timeout=55)
            stopped = docker_inspect(info["Id"], app, paths)["State"]
            if stopped["Running"]:
                raise BackupError("container_stop_unconfirmed")
            if stopped.get("ExitCode") == 137 or stopped.get("OOMKilled"):
                raise BackupError("unclean_container_stop")
        # Graceful shutdown may finish a pipeline job after the initial estimate.
        # Recheck the now-stable tree before allocating an uncompressed archive.
        plain_tree(root, max_bytes=MAX_STATE_BYTES)
        if shutil.disk_usage(output.parent).free < estimate_archive_bytes([(root, ".")]) + SPACE_RESERVE_BYTES:
            raise BackupError("staging_space_insufficient")
        make_archive(output, [(root, ".")], seconds=CAPTURE_SECONDS)
    finally:
        if running:
            recover_running(paths)
    # Hashing and all larger backup work occur after restarting the application.
    return {"captured_at": utc_now(), "was_running": running, "image_id": info["Image"],
            "members": archive_inventory(output, app=app)}


def recovery_sources(paths: Paths) -> list[tuple[Path, str]]:
    required = [
        (paths.runtime / "deploy/stirling-pdf.env", "runtime/deploy/stirling-pdf.env"),
        (paths.runtime / "deploy/changedetection.env", "runtime/deploy/changedetection.env"),
        (paths.runtime / "proxy.json", "runtime/proxy.json"),
        (paths.mount_guard, "installed/mount-uuid"),
    ]
    for name in ("edsys-private-web-apps.service", "edsys-private-web-apps-backup.service",
                 "edsys-private-web-apps-backup.timer"):
        required.append((paths.system_units / name, "installed/system/" + name))
    required.append((paths.user_units / "edsys-private-web-apps-proxy.service",
                     "installed/user/edsys-private-web-apps-proxy.service"))
    for name in ("edsys-private-web-apps-control", "edsys-private-web-apps-mount-check",
                 "edsys-private-web-apps-backup"):
        required.append((paths.libexec / name, "installed/libexec/" + name))
    for source, _ in required:
        if not source.is_file() or source.is_symlink() or not source.stat().st_size:
            raise BackupError("recovery_artifact_missing")
    # Preserve every non-ephemeral top-level runtime entry, not just today's envs.
    sources = [(p, n) for p, n in required if not n.startswith("runtime/")]
    for child in sorted(paths.runtime.iterdir()):
        if child.name in (*APPS, "images", "tmp", "candidate", ".tmp", ".candidate"):
            continue
        if child.name.startswith(("tmp-", "candidate-", ".tmp-", ".candidate-")):
            continue
        if child.is_symlink() or not (child.is_file() or child.is_dir()):
            raise BackupError("unsupported_runtime_artifact")
        sources.append((child, "runtime/" + child.name))
    source_requirements = {
        "docker/stirling-pdf": "compose.yaml",
        "docker/changedetection": "compose.yaml",
        "services/private-web-apps": "backup.py",
        "services/stirling-mcp": "server.py",
    }
    for relative, required_name in source_requirements.items():
        root = paths.source / relative
        plain_tree(root, max_bytes=128 * 1024**2)
        if not (root / required_name).is_file() or not (root / required_name).stat().st_size:
            raise BackupError("recovery_source_missing")
        sources.append((root, "source/" + relative))
    return sources


def image_inventory(root: Path) -> list[dict]:
    files = plain_tree(root, max_bytes=MAX_IMAGE_BYTES)
    archives = [p for p in files if p.name.endswith((".tar", ".tar.gz", ".tar.zst")) and p.stat().st_size]
    if len(archives) < 2:
        raise BackupError("two_image_archives_required")
    with bounded(1200):
        return [file_record(p, str(p.relative_to(root))) for p in files]


def verify_image_bindings(root: Path, images: list[dict], apps: dict) -> None:
    """Bind captured Docker image IDs to retained exports, not opaque artifacts."""
    index_path = root / "archives.json"
    if not index_path.is_file() or not 0 < index_path.stat().st_size <= 1024**2:
        raise BackupError("image_archive_index_missing")
    try:
        entries = json.loads(index_path.read_text())
        if not isinstance(entries, list) or not entries:
            raise BackupError("image_archive_index_invalid")
        inventory = {item["path"]: item for item in images}
        exported_ids = set()
        filenames = set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"file", "image_id", "sha256", "size"}:
                raise BackupError("image_archive_index_invalid")
            filename, image_id = entry["file"], entry["image_id"]
            if (not isinstance(filename, str) or PurePosixPath(filename).name != filename
                    or not filename.endswith((".tar", ".tar.gz", ".tar.zst")) or filename in filenames
                    or not isinstance(image_id, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id)
                    or not isinstance(entry["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])
                    or type(entry["size"]) is not int or entry["size"] <= 0):
                raise BackupError("image_archive_index_invalid")
            actual = inventory.get(filename)
            if not actual or any(actual[key] != entry[key] for key in ("sha256", "size")):
                raise BackupError("image_export_inventory_mismatch")
            filenames.add(filename)
            exported_ids.add(image_id)
        for app in APPS:
            image_id = apps[app].get("image_id")
            if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
                raise BackupError("captured_image_id_invalid")
            if image_id not in exported_ids:
                raise BackupError("active_image_export_missing")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise BackupError("image_archive_index_invalid") from None


def verify_stage(stage: Path, image_root: Path) -> dict:
    try:
        if {p.name for p in stage.iterdir()} != PAYLOAD_FILES:
            raise BackupError("payload_file_set_mismatch")
        manifest = json.loads((stage / "manifest.json").read_text())
        if manifest.get("schema") != 1 or manifest.get("scope") != SCOPE:
            raise BackupError("manifest_schema_mismatch")
        if set(manifest.get("apps", {})) != set(APPS):
            raise BackupError("manifest_apps_mismatch")
        if set(manifest.get("archives", {})) != PAYLOAD_FILES - {"manifest.json"}:
            raise BackupError("manifest_archive_set_mismatch")
        total = 0
        for name, record in manifest["archives"].items():
            actual = file_record(stage / name, name)
            if actual != record:
                raise BackupError("archive_hash_or_mode_mismatch")
            app = name[:-4] if name[:-4] in APPS else None
            members = archive_inventory(stage / name, app=app)
            expected = manifest["apps"][app]["members"] if app else manifest["recovery_members"]
            if members != expected:
                raise BackupError("archive_inventory_mismatch")
            total += sum(m.get("size", 0) for m in members)
        images = image_inventory(image_root)
        if images != manifest.get("images"):
            raise BackupError("image_hash_or_mode_mismatch")
        verify_image_bindings(image_root, images, manifest["apps"])
        return {"app_count": len(APPS), "member_count": sum(len(a["members"]) for a in manifest["apps"].values()),
                "state_and_recovery_bytes": total, "image_count": len(images),
                "image_bytes": sum(i["size"] for i in images)}
    except (OSError, ValueError, KeyError, TypeError):
        raise BackupError("stage_invalid") from None


def load_environment(config: Path) -> dict:
    mode = config.stat()
    if mode.st_uid != 0 or mode.st_mode & 0o022:
        raise BackupError("backup_config_not_trusted")
    # Source the existing trusted config, returning only path/settings variables.
    script = "import json,os; print(json.dumps({k:os.environ.get(k,'') for k in " + repr(CONFIG_KEYS) + "}))"
    output = run(["/bin/bash", "-c", 'set -a; source "$1"; exec /usr/bin/python3 -c "$2"',
                  "private-web-apps-config", str(config), script], label="backup_config", timeout=10)
    values = json.loads(output)
    defaults = {"RESTIC_REPOSITORY": "/srv/edsys-backup/restic-repo/edsys-critical",
                "RESTIC_PASSWORD_FILE": "/etc/edsys-backup/restic-password",
                "RESTIC_CACHE_DIR": "/var/cache/edsys-backup/restic", "RESTIC_LOCK_RETRY": "5m",
                "RCLONE_CONFIG": "/etc/edsys-backup/rclone.conf",
                "RCLONE_OFFSITE_DEST": "edsys-gdrive:EdSys Backups/restic/edsys-critical-v3"}
    environment = os.environ.copy()
    environment.update({k: values.get(k) or v for k, v in defaults.items()})
    if values.get("RCLONE_BIN"):
        environment["RCLONE_BIN"] = values["RCLONE_BIN"]
    return environment


class Repository:
    def __init__(self, environment: dict, *, offsite: bool = False):
        self.environment = environment
        self.offsite = offsite

    def call(self, arguments: list[str], *, timeout: int = 1800) -> bytes:
        command = ["restic", "--no-cache"]
        if self.offsite:
            command += ["--no-lock", "--repo", "rclone:" + self.environment["RCLONE_OFFSITE_DEST"]]
            if self.environment.get("RCLONE_BIN"):
                command += ["-o", "rclone.program=" + self.environment["RCLONE_BIN"]]
        else:
            command += ["--retry-lock", self.environment["RESTIC_LOCK_RETRY"]]
        return run(command + arguments, label="restic", timeout=timeout, env=self.environment)

    def metadata(self, snapshot: str, paths: Paths) -> dict:
        if not re.fullmatch(r"[a-f0-9]{64}", snapshot):
            raise BackupError("full_snapshot_id_required")
        try:
            records = json.loads(self.call(["snapshots", "--json", snapshot], timeout=120))
            record, = records
            wanted = {str(paths.stage / "snapshot-payload"), str(paths.runtime / "images")}
            if record["id"] != snapshot or not {SCOPE, FORMAT_TAG}.issubset(record.get("tags", [])):
                raise BackupError("snapshot_scope_mismatch")
            if set(record.get("paths", [])) != wanted:
                raise BackupError("snapshot_path_mismatch")
            return record
        except (ValueError, KeyError, TypeError):
            raise BackupError("snapshot_metadata_invalid") from None

    def backup(self, payload: Path, paths: Paths, generation: str) -> str:
        output = self.call(["backup", "--json", "--tag", SCOPE, "--tag", FORMAT_TAG,
                            "--tag", generation, "--", str(payload), str(paths.runtime / "images")])
        try:
            summaries = [json.loads(line) for line in output.splitlines() if line]
            short_id = next(x["snapshot_id"] for x in summaries if x.get("message_type") == "summary")
            records = json.loads(self.call(["snapshots", "--json", short_id], timeout=120))
            record, = records
            snapshot = record["id"]
        except (ValueError, KeyError, StopIteration, TypeError):
            raise BackupError("snapshot_id_missing") from None
        metadata = self.metadata(snapshot, paths)
        if generation not in metadata.get("tags", []):
            raise BackupError("snapshot_generation_mismatch")
        # Prove the exact new snapshot contains its required manifest and files.
        saved = self.call(["dump", snapshot, str(payload / "manifest.json")], timeout=120)
        if hashlib.sha256(saved).hexdigest() != hash_file(payload / "manifest.json"):
            raise BackupError("snapshot_manifest_mismatch")
        listing = self.call(["ls", "--json", snapshot], timeout=120)
        nodes = {n["path"]: n for line in listing.splitlines() if line
                 for n in [json.loads(line)] if n.get("type") == "file"}
        expected = {str(p): p.stat().st_size for p in payload.iterdir()}
        expected.update({str(p): p.stat().st_size for p in plain_tree(paths.runtime / "images", max_bytes=MAX_IMAGE_BYTES)})
        if set(nodes) != set(expected) or any(nodes[p].get("size") != size for p, size in expected.items()):
            raise BackupError("snapshot_file_inventory_mismatch")
        return snapshot


def prepare_generation(paths: Paths, generation: str, repository_path: Path | None = None) -> Path:
    incoming = paths.stage / ".incoming" / generation
    incoming.mkdir(parents=True, mode=0o700)
    sources = recovery_sources(paths)  # Refuse missing recovery prerequisites before stopping anything.
    images = image_inventory(paths.runtime / "images")
    space = preflight_space(paths, sources, images, repository_path)
    apps = {app: capture_app(app, incoming / (app + ".tar"), paths) for app in APPS}
    make_archive(incoming / "recovery.tar", sources, seconds=120)
    manifest = {"schema": 1, "scope": SCOPE, "generation": generation, "created_at": utc_now(),
                "space_preflight": space,
                "capture_limits": {"state_bytes_per_archive": MAX_STATE_BYTES, "cold_seconds": CAPTURE_SECONDS},
                "apps": apps, "images": images,
                "recovery_members": archive_inventory(incoming / "recovery.tar"),
                "archives": {name: file_record(incoming / name, name)
                             for name in sorted(PAYLOAD_FILES - {"manifest.json"})}}
    atomic_json(incoming / "manifest.json", manifest)
    verify_stage(incoming, paths.runtime / "images")
    destination = paths.stage / "generations" / generation
    destination.parent.mkdir(mode=0o700, exist_ok=True)
    os.rename(incoming, destination)
    return destination


def accepted_generation(paths: Paths, directory: Path) -> tuple[dict, dict, dt.datetime] | None:
    """Recognize only this helper's accepted, unchanged staging structure."""
    try:
        if (not re.fullmatch(GENERATION_PATTERN, directory.name) or directory.is_symlink()
                or not directory.is_dir() or directory.resolve().parent != (paths.stage / "generations").resolve()
                or directory.stat().st_uid != os.geteuid() or directory.stat().st_mode & 0o077
                or {p.name for p in directory.iterdir()} != PAYLOAD_FILES):
            return None
        receipt_path = paths.stage / "accepted" / (directory.name + ".json")
        if receipt_path.is_symlink() or not receipt_path.is_file():
            return None
        if receipt_path.stat().st_uid != os.geteuid() or receipt_path.stat().st_mode & 0o077:
            return None
        receipt = json.loads(receipt_path.read_text())
        if (receipt.get("schema") != 1 or receipt.get("scope") != SCOPE
                or receipt.get("status") != "success" or receipt.get("generation") != directory.name
                or not re.fullmatch(r"[a-f0-9]{64}", receipt.get("snapshot_id", ""))):
            return None
        manifest_path = directory / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            return None
        if hash_file(manifest_path) != receipt.get("manifest_sha256"):
            return None
        manifest = json.loads(manifest_path.read_text())
        if (manifest.get("schema") != 1 or manifest.get("scope") != SCOPE
                or manifest.get("generation") != directory.name
                or set(manifest.get("apps", {})) != set(APPS)
                or set(manifest.get("archives", {})) != PAYLOAD_FILES - {"manifest.json"}):
            return None
        for name, record in manifest["archives"].items():
            info = (directory / name).lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or record.get("path") != name
                    or info.st_size != record.get("size") or stat.S_IMODE(info.st_mode) != record.get("mode")
                    or info.st_uid != record.get("uid") or info.st_gid != record.get("gid")):
                return None
        completed = dt.datetime.fromisoformat(receipt["completed_at"])
        if completed.tzinfo is None:
            return None
        return receipt, manifest, completed
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def cleanup_accepted_generations(paths: Paths) -> dict:
    """Keep CURRENT and two older accepted generations; ignore all unknown data."""
    generations = paths.stage / "generations"
    accepted = paths.stage / "accepted"
    if generations.is_symlink() or accepted.is_symlink():
        raise BackupError("staging_cleanup_ownership_unconfirmed")
    current = json.loads((paths.stage / "CURRENT.json").read_text())
    name = current.get("generation", "")
    if not re.fullmatch(GENERATION_PATTERN, name):
        raise BackupError("staging_cleanup_current_unconfirmed")
    recognized = accepted_generation(paths, generations / name)
    if recognized is None:
        raise BackupError("staging_cleanup_current_unconfirmed")
    receipt, _, completed = recognized
    if (current.get("snapshot_id") != receipt["snapshot_id"]
            or current.get("manifest_sha256") != receipt["manifest_sha256"]
            or hash_file(paths.stage / "snapshot-payload/manifest.json") != receipt["manifest_sha256"]):
        raise BackupError("staging_cleanup_payload_unconfirmed")
    older = []
    ignored = 0
    for directory in generations.iterdir():
        if directory.name == name:
            continue
        item = accepted_generation(paths, directory)
        if item is None or item[2] >= completed:
            ignored += 1
            continue
        older.append((item[2], directory, item[1]))
    removed = 0
    for _, directory, manifest in sorted(older, key=lambda item: item[0], reverse=True)[2:]:
        try:
            # Revalidate byte hashes before deleting an old accepted archive.
            with bounded(600):
                if any(file_record(directory / name, name) != record
                       for name, record in manifest["archives"].items()):
                    ignored += 1
                    continue
        except (BackupError, OSError):
            ignored += 1
            continue
        # Explicit regular-file names only; never recursively traverse a tree.
        for filename in sorted(PAYLOAD_FILES - {"manifest.json"}) + ["manifest.json"]:
            (directory / filename).unlink()
        directory.rmdir()
        (accepted / (directory.name + ".json")).unlink()
        removed += 1
    return {"status": "complete", "removed_generations": removed, "ignored_generations": ignored}


def backup(paths: Paths, repository: Repository) -> dict:
    paths.stage.mkdir(mode=0o700, parents=True, exist_ok=True)
    if paths.stage.is_symlink():
        raise BackupError("stage_symlink_refused")
    recover_running(paths)
    generation = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    generation_path = prepare_generation(paths, generation, Path(repository.environment["RESTIC_REPOSITORY"]))
    payload = paths.stage / "snapshot-payload"
    previous = paths.stage / (".previous-payload-" + generation)
    candidate = paths.stage / (".new-payload-" + generation)
    candidate.mkdir(mode=0o700)
    for name in PAYLOAD_FILES:
        os.link(generation_path / name, candidate / name)
    had_previous = payload.exists()
    if had_previous and (payload.is_symlink() or not payload.is_dir()):
        raise BackupError("payload_directory_invalid")
    try:
        if had_previous:
            os.rename(payload, previous)
        os.rename(candidate, payload)
        summary = verify_stage(payload, paths.runtime / "images")
        snapshot = repository.backup(payload, paths, generation)
        # Recheck captured inputs after Restic; mutable image archives cannot be accepted.
        verify_stage(payload, paths.runtime / "images")
        receipt = {"schema": 1, "scope": SCOPE, "status": "success", "generation": generation,
                   "snapshot_id": snapshot, "completed_at": utc_now(),
                   "manifest_sha256": hash_file(payload / "manifest.json"), **summary}
        atomic_json(paths.stage / "CURRENT.json", receipt)
    except BaseException:
        if previous.exists():
            if payload.exists():
                shutil.rmtree(payload)
            os.rename(previous, payload)
        elif not had_previous and payload.exists():
            shutil.rmtree(payload)
        raise
    # CURRENT is the acceptance boundary. Housekeeping cannot invalidate the
    # verified snapshot or cause deletion of an unaccepted generation.
    cleanup = {"status": "deferred"}
    try:
        if had_previous:
            shutil.rmtree(previous)
        accepted = paths.stage / "accepted"
        if accepted.is_symlink():
            raise BackupError("staging_cleanup_ownership_unconfirmed")
        atomic_json(accepted / (generation + ".json"), receipt)
        cleanup = cleanup_accepted_generations(paths)
    except (BackupError, OSError, ValueError, KeyError, TypeError):
        pass
    result = {**receipt, "staging_cleanup": cleanup}
    with contextlib.suppress(OSError):
        atomic_json(paths.stage / "last-attempt.json", result)
    return result


def restore(paths: Paths, repository: Repository, snapshot: str, target: Path) -> dict:
    repository.metadata(snapshot, paths)
    if not target.is_absolute():
        raise BackupError("absolute_restore_target_required")
    resolved = target.resolve()
    for forbidden in (paths.runtime.resolve(), paths.stage.resolve()):
        if resolved == forbidden or forbidden in resolved.parents or resolved in forbidden.parents:
            raise BackupError("live_restore_target_refused")
    if target.exists():
        raise BackupError("restore_target_must_be_new")
    target.mkdir(mode=0o700, parents=True)
    repository.call(["restore", snapshot, "--target", str(target), "--verify"], timeout=2400)
    payload = target / str(paths.stage / "snapshot-payload").lstrip("/")
    images = target / str(paths.runtime / "images").lstrip("/")
    summary = verify_stage(payload, images)
    return {"status": "success", "snapshot_id": snapshot, "offsite": repository.offsite, **summary}


@contextlib.contextmanager
def backup_lock(path: Path):
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError("backup_lock_busy") from None
        yield


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("backup", "verify", "restore", "recover-running", "cleanup-staging", "status"))
    parser.add_argument("--config", type=Path, default=Path("/etc/edsys-backup/edsys-backup.conf"))
    parser.add_argument("--stage", type=Path)
    parser.add_argument("--image-root", type=Path)
    parser.add_argument("--snapshot")
    parser.add_argument("--target", type=Path)
    parser.add_argument("--offsite", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    paths = Paths()
    def terminated(_signal, _frame):
        raise BackupError("terminated")
    signal.signal(signal.SIGTERM, terminated)
    try:
        if os.geteuid() != 0:
            raise BackupError("root_required")
        if args.command == "verify":
            summary = verify_stage(args.stage or paths.stage / "snapshot-payload", args.image_root or paths.runtime / "images")
            result = {"status": "success", **summary}
        elif args.command == "status":
            result = json.loads((paths.stage / "CURRENT.json").read_text())
        elif args.command == "recover-running":
            with backup_lock(paths.lock):
                result = {"status": "success", "restarted": recover_running(paths)}
        elif args.command == "cleanup-staging":
            with backup_lock(paths.lock):
                result = cleanup_accepted_generations(paths)
        else:
            environment = load_environment(args.config)
            repository = Repository(environment, offsite=args.offsite)
            if args.command == "restore":
                if not args.snapshot or not args.target:
                    raise BackupError("restore_snapshot_and_target_required")
                result = restore(paths, repository, args.snapshot, args.target)
            else:
                if args.offsite:
                    raise BackupError("backup_writes_are_local_only")
                with backup_lock(paths.lock):
                    run([str(paths.libexec / "edsys-private-web-apps-mount-check")], label="mount_guard", timeout=15)
                    result = backup(paths, repository)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (BackupError, OSError, ValueError, KeyError) as error:
        code = str(error) if isinstance(error, BackupError) else type(error).__name__
        if args.command == "backup":
            with contextlib.suppress(OSError):
                atomic_json(paths.stage / "last-attempt.json", {"status": "failed", "code": code, "attempted_at": utc_now()})
        print(json.dumps({"status": "failed", "code": code}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
