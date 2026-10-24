#!/usr/bin/env python3
"""Refuse Immich startup unless its two exact local filesystems are mounted."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess


class Refused(RuntimeError):
    pass


def validate_mount(row: dict, target: str, expected_uuid: str) -> None:
    if row.get("target") != target or row.get("uuid") != expected_uuid:
        raise Refused("required_mount_identity_missing")
    if row.get("fstype") != "ext4":
        raise Refused("required_local_ext4_missing")
    options = set(row.get("options", "").split(","))
    if "rw" not in options:
        raise Refused("required_mount_not_writable")


def mount_identity(target: str) -> dict:
    try:
        result = subprocess.run(
            ["findmnt", "--json", "--mountpoint", target, "--output",
             "TARGET,UUID,FSTYPE,OPTIONS"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        rows = json.loads(result.stdout)["filesystems"]
        if len(rows) != 1:
            raise Refused("required_mount_identity_missing")
        return rows[0]
    except (subprocess.SubprocessError, ValueError, KeyError) as exc:
        raise Refused("required_mount_identity_missing") from exc


def validate_directory(path: Path, root: Path) -> None:
    if not path.is_dir() or path.is_symlink() or path.resolve() != path:
        raise Refused("required_data_directory_missing_or_symlinked")
    if not path.is_relative_to(root) or path.stat().st_dev != root.stat().st_dev:
        raise Refused("data_directory_on_wrong_filesystem")


def validate_postgres_cluster(path: Path) -> None:
    version, control = path / "PG_VERSION", path / "global" / "pg_control"
    if (version.is_symlink() or not version.is_file() or version.read_text().strip() != "14"
            or control.is_symlink() or not control.is_file() or control.stat().st_size != 8192):
        raise Refused("required_existing_postgres_cluster_missing")


def check(media: str, media_uuid: str, nvme: str, nvme_uuid: str) -> None:
    for target, expected in ((media, media_uuid), (nvme, nvme_uuid)):
        validate_mount(mount_identity(target), target, expected)
    media_root, nvme_root = Path(media), Path(nvme)
    for path in (media_root / "upload", media_root / "legacy-library"):
        validate_directory(path, media_root)
    for name in ("postgres", "redis", "config", "server-data", "model-cache", "logs"):
        validate_directory(nvme_root / "immich" / name, nvme_root)
    validate_postgres_cluster(nvme_root / "immich" / "postgres")
    marker = media_root / ".edsys-immich-volume"
    if marker.is_symlink() or not marker.is_file() or marker.read_text().strip() != media_uuid:
        raise Refused("required_media_marker_missing")
    if not os.access(media_root / "upload", os.R_OK | os.W_OK):
        raise Refused("media_unwritable")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media", default="/mnt/immich-library")
    parser.add_argument("--media-uuid", required=True)
    parser.add_argument("--nvme", default="/mnt/ai-store")
    parser.add_argument("--nvme-uuid", required=True)
    args = parser.parse_args()
    try:
        check(args.media, args.media_uuid, args.nvme, args.nvme_uuid)
    except (Refused, OSError) as exc:
        print("Immich startup refused: " + (str(exc) if isinstance(exc, Refused) else "filesystem_check_failed"))
        return 1
    print("Immich storage identities verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
