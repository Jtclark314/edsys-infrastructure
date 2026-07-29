#!/usr/bin/env python3
"""Trigger, transfer, and independently verify the Basecamp recovery stage."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


REMOTE_CURRENT = r"C:\Foothills\OffsiteBackup\current"
REMOTE_MANIFEST = REMOTE_CURRENT + r"\manifest.json"
TASK_NAME = "Foothills Offsite Backup"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(
    command: list[str],
    *,
    stdout: int | Any = subprocess.PIPE,
    timeout: int | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        check=True,
        stdout=stdout,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def ssh(remote: str, command: str, timeout: int = 60) -> str:
    result = run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=20",
            "-o",
            "ServerAliveInterval=10",
            "-o",
            "ServerAliveCountMax=3",
            remote,
            command,
        ],
        timeout=timeout,
    )
    return result.stdout.decode("utf-8-sig", errors="strict")


def remote_manifest(remote: str) -> dict[str, Any] | None:
    try:
        raw = ssh(remote, f'cmd.exe /d /c type "{REMOTE_MANIFEST}"')
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def parse_created_at(manifest: dict[str, Any]) -> datetime:
    value = str(manifest["created_at"])
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def manifest_age_hours(manifest: dict[str, Any]) -> float:
    return (datetime.now(timezone.utc) - parse_created_at(manifest)).total_seconds() / 3600


def trigger_remote_backup(remote: str, timeout_seconds: int) -> dict[str, Any]:
    # EncodedCommand avoids nested quoting through Windows OpenSSH.
    script = f"""
$ErrorActionPreference='Stop'
$before=$null
$manifest='{REMOTE_MANIFEST.replace("'", "''")}'
if(Test-Path -LiteralPath $manifest){{$before=(Get-Item -LiteralPath $manifest).LastWriteTimeUtc}}
Start-ScheduledTask -TaskName '{TASK_NAME}'
$deadline=(Get-Date).AddSeconds({timeout_seconds})
do{{Start-Sleep -Seconds 3;$task=Get-ScheduledTask -TaskName '{TASK_NAME}'}}while($task.State -eq 'Running' -and (Get-Date) -lt $deadline)
if($task.State -eq 'Running'){{throw 'Basecamp backup task timed out'}}
$info=Get-ScheduledTaskInfo -TaskName '{TASK_NAME}'
if($info.LastTaskResult -ne 0){{throw ('Basecamp backup task failed: '+$info.LastTaskResult)}}
if(-not (Test-Path -LiteralPath $manifest)){{throw 'Basecamp backup manifest is missing'}}
$after=(Get-Item -LiteralPath $manifest).LastWriteTimeUtc
if($before -and $after -le $before){{throw 'Basecamp backup manifest was not refreshed'}}
Get-Content -LiteralPath $manifest -Raw
"""
    import base64

    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    raw = ssh(
        remote,
        f"powershell.exe -NoProfile -NonInteractive -EncodedCommand {encoded}",
        timeout=timeout_seconds + 60,
    )
    start = raw.find("{")
    if start < 0:
        raise RuntimeError("Basecamp task returned no manifest")
    return json.loads(raw[start:])


def safe_tar_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    for member in members:
        path = PurePosixPath(member.name.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError(f"Unsafe tar member: {member.name}")
        if member.issym() or member.islnk() or member.isdev():
            raise RuntimeError(f"Unsupported tar member: {member.name}")
    return members


def download_stage(remote: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="basecamp-transfer-", dir=destination.parent) as temporary:
        temporary_path = Path(temporary)
        archive_path = temporary_path / "basecamp-stage.tar"
        with archive_path.open("wb") as output:
            run(
                [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=20",
                    "-o",
                    "ServerAliveInterval=10",
                    "-o",
                    "ServerAliveCountMax=6",
                    remote,
                    r'cmd.exe /d /c tar.exe -cf - -C "C:\Foothills\OffsiteBackup\current" .',
                ],
                stdout=output,
                timeout=7200,
            )
        candidate = destination.parent / f".candidate-{os.getpid()}"
        if candidate.exists():
            shutil.rmtree(candidate)
        candidate.mkdir(parents=True)
        try:
            with tarfile.open(archive_path, "r:") as archive:
                members = safe_tar_members(archive)
                archive.extractall(candidate, members=members, filter="data")
            verify_stage(candidate)
            previous = destination.parent / "previous"
            stale = destination.parent / f".stale-{os.getpid()}"
            if stale.exists():
                shutil.rmtree(stale)
            if previous.exists():
                os.replace(previous, stale)
            if destination.exists():
                os.replace(destination, previous)
            os.replace(candidate, destination)
            if stale.exists():
                shutil.rmtree(stale)
        except Exception:
            if candidate.exists():
                shutil.rmtree(candidate)
            raise


def sqlite_check(path: Path) -> None:
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"]:
            raise RuntimeError(f"SQLite integrity failure: {path}")
        if list(connection.execute("PRAGMA foreign_key_check")):
            raise RuntimeError(f"SQLite foreign-key failure: {path}")
    finally:
        connection.close()


def zip_check(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            candidate = PurePosixPath(name.replace("\\", "/"))
            if candidate.is_absolute() or ".." in candidate.parts:
                raise RuntimeError(f"Unsafe ZIP path in {path}: {name}")
        failed = archive.testzip()
        if failed:
            raise RuntimeError(f"ZIP CRC failure in {path}: {failed}")


def verify_stage(stage: Path, max_age_hours: float | None = None) -> dict[str, Any]:
    manifest_path = stage / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("schema_version") != 1 or manifest.get("status") != "verified":
        raise RuntimeError("Unsupported or unverified Basecamp manifest")
    if max_age_hours is not None and manifest_age_hours(manifest) > max_age_hours:
        raise RuntimeError(
            f"Basecamp backup is stale: {manifest_age_hours(manifest):.2f} hours"
        )
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise RuntimeError("Basecamp manifest has no file list")
    expected = set()
    total_bytes = 0
    for entry in entries:
        relative = PurePosixPath(str(entry["path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError(f"Unsafe manifest path: {relative}")
        expected.add(relative.as_posix())
        path = stage.joinpath(*relative.parts)
        if not path.is_file():
            raise RuntimeError(f"Missing staged file: {relative}")
        if path.stat().st_size != int(entry["size"]):
            raise RuntimeError(f"Staged file size mismatch: {relative}")
        if sha256_file(path) != entry["sha256"]:
            raise RuntimeError(f"Staged file hash mismatch: {relative}")
        total_bytes += path.stat().st_size
    actual = {
        path.relative_to(stage).as_posix()
        for path in stage.rglob("*")
        if path.is_file() and path.relative_to(stage).as_posix() != "manifest.json"
    }
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise RuntimeError(
            "Unexpected staged file set: "
            f"missing={len(missing)} {missing[:10]!r} "
            f"extra={len(extra)} {extra[:10]!r}"
        )
    if len(entries) != int(manifest["file_count"]) or total_bytes != int(manifest["total_bytes"]):
        raise RuntimeError("Manifest aggregate counts do not reconcile")

    for item in manifest.get("validations", {}).get("sqlite", []):
        sqlite_check(stage / item["relative_path"])
    for item in manifest.get("validations", {}).get("archives", []):
        relative = PurePosixPath(str(item["relative_path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError(f"Unsafe archive validation path: {relative}")
        archive_path = stage.joinpath(*relative.parts)
        if not archive_path.is_file():
            raise RuntimeError(f"Validated archive is missing: {relative}")
        zip_check(archive_path)
    portal = stage / "apps/project-portal/current-site/data/portal-content.json"
    json.loads(portal.read_text(encoding="utf-8-sig"))
    return manifest


def write_status(root: Path, manifest: dict[str, Any], transferred: bool) -> None:
    status = {
        "status": "success",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "generation": manifest["generation"],
        "created_at": manifest["created_at"],
        "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
        "transferred": transferred,
        "manifest_sha256": sha256_file(root / "current/manifest.json"),
    }
    temporary = root / f".status-{os.getpid()}.json"
    temporary.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, root / "last-success.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remote", default="basecamp")
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path("/mnt/ai-store/foothills-basecamp-offsite"),
    )
    parser.add_argument("--remote-max-age-hours", type=float, default=1.5)
    parser.add_argument("--max-age-hours", type=float, default=30)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--task-timeout-seconds", type=int, default=5400)
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    arguments.destination.mkdir(parents=True, exist_ok=True)
    os.chmod(arguments.destination, 0o700)
    current = arguments.destination / "current"
    if arguments.verify_only:
        manifest = verify_stage(current, max_age_hours=arguments.max_age_hours)
        write_status(arguments.destination, manifest, transferred=False)
        print(
            f"verified generation={manifest['generation']} files={manifest['file_count']} "
            f"bytes={manifest['total_bytes']}"
        )
        return 0

    manifest = remote_manifest(arguments.remote)
    if manifest is None or manifest_age_hours(manifest) > arguments.remote_max_age_hours:
        manifest = trigger_remote_backup(arguments.remote, arguments.task_timeout_seconds)
    if manifest_age_hours(manifest) > arguments.remote_max_age_hours:
        raise RuntimeError("Basecamp did not publish a fresh backup generation")

    transferred = True
    if current.exists():
        try:
            local = verify_stage(current, max_age_hours=arguments.max_age_hours)
            if local["generation"] == manifest["generation"]:
                transferred = False
        except Exception:
            transferred = True
    if transferred:
        download_stage(arguments.remote, current)
    verified = verify_stage(current, max_age_hours=arguments.max_age_hours)
    if verified["generation"] != manifest["generation"]:
        raise RuntimeError("Transferred generation differs from Basecamp manifest")
    write_status(arguments.destination, verified, transferred=transferred)
    print(
        f"verified generation={verified['generation']} files={verified['file_count']} "
        f"bytes={verified['total_bytes']} transferred={str(transferred).lower()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
