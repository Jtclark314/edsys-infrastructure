#!/usr/bin/env python3
"""Safely extract one forced-command EdCore Automation backup archive."""

from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile


MAX_MEMBERS = 100_000


class ExtractionError(RuntimeError):
    """The streamed archive is unsafe or incompatible."""


def canonical_member_name(value: str) -> str | None:
    if value == ".":
        return None
    while value.startswith("./"):
        value = value[2:]
    path = PurePosixPath(value)
    if (
        not value
        or "\x00" in value
        or path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ExtractionError(f"archive contains an unsafe path: {value!r}")
    return path.as_posix()


def extract_archive(archive_path: Path, destination: Path) -> int:
    archive_info = archive_path.lstat()
    if not stat.S_ISREG(archive_info.st_mode) or archive_path.is_symlink():
        raise ExtractionError("incoming archive must be a regular, non-symlink file")
    if destination.is_symlink() or not destination.is_dir() or any(destination.iterdir()):
        raise ExtractionError("destination must be an empty, non-symlink directory")

    with tarfile.open(archive_path, mode="r:*") as archive:
        members = archive.getmembers()
        if not members or len(members) > MAX_MEMBERS:
            raise ExtractionError("archive member count is invalid")
        names: set[str] = set()
        for member in members:
            name = canonical_member_name(member.name)
            if name is None:
                if not member.isdir():
                    raise ExtractionError("archive root entry is not a directory")
                continue
            if name in names:
                raise ExtractionError(f"archive contains a duplicate path: {name}")
            names.add(name)
            if not (member.isdir() or member.isreg()):
                raise ExtractionError(f"archive contains a link or special file: {name}")
        archive.extractall(destination, members=members, filter="data")

    for directory, dirnames, filenames in os.walk(destination, followlinks=False):
        base = Path(directory)
        for name in [*dirnames, *filenames]:
            candidate = base / name
            mode = candidate.lstat().st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ExtractionError(
                    f"extracted tree contains a link or special file: {candidate.relative_to(destination)}"
                )
    return len(names)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    count = extract_archive(args.archive, args.destination)
    print(f"PASS safe backup extraction: members={count}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ExtractionError, OSError, tarfile.TarError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
