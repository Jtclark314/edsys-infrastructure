#!/usr/bin/env python3
"""Verify an EdCore Automation application-consistent backup directory."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat


RUN_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
CHECKSUM_RE = re.compile(r"^([0-9a-f]{64})  \./(.+)$")
MANIFEST_SCHEMA = "edsys.edcore-automation.backup.v1"
MANIFEST_KEYS = {
    "schema",
    "run_id",
    "created_utc",
    "hostname",
    "compose_project",
    "artifacts",
    "artifact_count",
    "images",
    "services",
}
EXPECTED_SERVICES = [
    "automation-runtime",
    "influxdb",
    "mosquitto",
    "node-red",
    "telegraf",
]
METADATA_FILES = {"MANIFEST.json", "SHA256SUMS"}


class VerificationError(RuntimeError):
    """The staged backup does not satisfy the recovery contract."""


def safe_relative_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\n" in value:
        raise VerificationError(f"{label} is not a safe relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value.startswith("./")
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise VerificationError(f"{label} is not a canonical relative path: {value!r}")
    return path.as_posix()


def regular_files(root: Path) -> set[str]:
    result: set[str] = set()
    for directory, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        for dirname in dirnames:
            candidate = directory_path / dirname
            mode = candidate.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise VerificationError(f"backup contains a directory symlink: {candidate.relative_to(root)}")
            if not stat.S_ISDIR(mode):
                raise VerificationError(f"backup contains a non-directory entry: {candidate.relative_to(root)}")
        for filename in filenames:
            candidate = directory_path / filename
            mode = candidate.lstat().st_mode
            relative = candidate.relative_to(root).as_posix()
            if not stat.S_ISREG(mode):
                raise VerificationError(f"backup contains a non-regular file: {relative}")
            result.add(relative)
    return result


def require_private_metadata_file(path: Path, maximum_size: int) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise VerificationError(f"required metadata file is missing: {path.name}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise VerificationError(f"metadata entry is not a regular file: {path.name}")
    if info.st_size <= 0 or info.st_size > maximum_size:
        raise VerificationError(f"metadata file has an invalid size: {path.name}")


def load_manifest(path: Path, expected_run_id: str, payload_files: set[str]) -> dict:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"MANIFEST.json is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise VerificationError("MANIFEST.json must contain one JSON object")
    keys = set(manifest)
    if keys != MANIFEST_KEYS:
        raise VerificationError(
            "MANIFEST.json key mismatch: "
            f"missing={sorted(MANIFEST_KEYS - keys)} unexpected={sorted(keys - MANIFEST_KEYS)}"
        )
    if manifest["schema"] != MANIFEST_SCHEMA:
        raise VerificationError(f"unsupported manifest schema: {manifest['schema']!r}")
    if manifest["run_id"] != expected_run_id:
        raise VerificationError("manifest run_id does not match the selected backup directory")
    if manifest["hostname"] != "edcore-automation":
        raise VerificationError(f"unexpected backup hostname: {manifest['hostname']!r}")
    if manifest["compose_project"] != "edsys-edcore-automation":
        raise VerificationError(f"unexpected Compose project: {manifest['compose_project']!r}")
    if manifest["services"] != EXPECTED_SERVICES:
        raise VerificationError(f"service set mismatch: {manifest['services']!r}")

    created = manifest["created_utc"]
    if not isinstance(created, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z",
        created,
    ):
        raise VerificationError("created_utc must be RFC3339 UTC")
    try:
        created_at = datetime.fromisoformat(created[:-1] + "+00:00")
        run_started = datetime.strptime(expected_run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise VerificationError(f"manifest timestamp is invalid: {exc}") from exc
    elapsed = (created_at - run_started).total_seconds()
    if elapsed < 0 or elapsed > 24 * 60 * 60:
        raise VerificationError("created_utc is outside the selected backup run window")

    artifacts_raw = manifest["artifacts"]
    if not isinstance(artifacts_raw, list):
        raise VerificationError("artifacts must be a sorted JSON array")
    artifacts = [safe_relative_path(item, "artifact") for item in artifacts_raw]
    if artifacts != sorted(set(artifacts)):
        raise VerificationError("artifacts must be sorted and unique")
    if manifest["artifact_count"] != len(artifacts) or isinstance(manifest["artifact_count"], bool):
        raise VerificationError("artifact_count does not equal the artifacts array length")
    if set(artifacts) != payload_files:
        raise VerificationError(
            "artifact inventory mismatch: "
            f"missing={sorted(payload_files - set(artifacts))} "
            f"unexpected={sorted(set(artifacts) - payload_files)}"
        )

    images = manifest["images"]
    if (
        not isinstance(images, list)
        or not images
        or any(not isinstance(item, str) or not item.strip() or item != item.strip() for item in images)
        or images != sorted(set(images))
    ):
        raise VerificationError("images must be a non-empty, sorted, unique array of exact identities")
    return manifest


def load_checksums(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise VerificationError(f"SHA256SUMS is not valid UTF-8 text: {exc}") from exc
    if not lines:
        raise VerificationError("SHA256SUMS is empty")
    checksums: dict[str, str] = {}
    for number, line in enumerate(lines, 1):
        match = CHECKSUM_RE.fullmatch(line)
        if match is None:
            raise VerificationError(f"invalid SHA256SUMS line {number}")
        relative = safe_relative_path(match.group(2), f"checksum line {number}")
        if relative in checksums:
            raise VerificationError(f"duplicate SHA256SUMS entry: {relative}")
        checksums[relative] = match.group(1)
    return checksums


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def verify_backup(root: Path, expected_run_id: str) -> dict:
    if not RUN_ID_RE.fullmatch(expected_run_id):
        raise VerificationError("expected run ID is not canonical UTC YYYYMMDDTHHMMSSZ")
    if root.is_symlink() or not root.is_dir():
        raise VerificationError("backup root must be an existing, non-symlink directory")

    manifest_path = root / "MANIFEST.json"
    checksums_path = root / "SHA256SUMS"
    require_private_metadata_file(manifest_path, 1024 * 1024)
    require_private_metadata_file(checksums_path, 16 * 1024 * 1024)

    files = regular_files(root)
    payload_files = files - METADATA_FILES
    if not payload_files:
        raise VerificationError("backup contains no payload artifacts")
    manifest = load_manifest(manifest_path, expected_run_id, payload_files)
    checksums = load_checksums(checksums_path)
    expected_hashed_files = files - {"SHA256SUMS"}
    if set(checksums) != expected_hashed_files:
        raise VerificationError(
            "checksum inventory mismatch: "
            f"missing={sorted(expected_hashed_files - set(checksums))} "
            f"unexpected={sorted(set(checksums) - expected_hashed_files)}"
        )
    for relative in sorted(checksums):
        actual = sha256(root / relative)
        if actual != checksums[relative]:
            raise VerificationError(f"SHA-256 mismatch: {relative}")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--expected-run-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = verify_backup(args.directory, args.expected_run_id)
    print(
        "PASS EdCore Automation backup: "
        f"run_id={manifest['run_id']} artifacts={manifest['artifact_count']} "
        f"services={len(manifest['services'])}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
