"""Inspect and safely extract one bounded EdCore secret-escrow tar archive.

This helper is installed root-owned on the 9950x recovery host. All metadata
is accepted before any destination exists; only validated directories and
regular files are then created manually beneath that new destination.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import sys
import tarfile
from typing import BinaryIO


MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_MEMBERS = 512
MAX_MEMBER_BYTES = 4 * 1024 * 1024
MAX_TOTAL_FILE_BYTES = 16 * 1024 * 1024
BLOCK_SIZE = 512
SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class ArchiveValidationError(ValueError):
    """A safe archive-contract rejection."""


@dataclass(frozen=True)
class AcceptedMember:
    name: str
    kind: str
    mode: int
    size: int
    info: tarfile.TarInfo


def _round_block(value: int) -> int:
    return ((value + BLOCK_SIZE - 1) // BLOCK_SIZE) * BLOCK_SIZE


def _canonical_name(raw_name: str) -> tuple[str, tuple[str, ...]]:
    if not isinstance(raw_name, str) or not raw_name or len(raw_name) > 1024:
        raise ArchiveValidationError("archive member has an invalid name")
    if raw_name.startswith("/") or "\\" in raw_name or "\x00" in raw_name:
        raise ArchiveValidationError("archive member path is absolute or ambiguous")
    candidate = raw_name[:-1] if raw_name.endswith("/") else raw_name
    if not candidate or raw_name.endswith("//"):
        raise ArchiveValidationError("archive member path is empty or ambiguous")
    parts = tuple(candidate.split("/"))
    if (
        any(part in {"", ".", ".."} for part in parts)
        or any(not SAFE_COMPONENT.fullmatch(part) for part in parts)
        or parts[0] != "edcore-automation"
    ):
        raise ArchiveValidationError("archive member escaped the fixed secret root")
    canonical = "/".join(parts)
    if len(canonical.encode("utf-8")) > 1024:
        raise ArchiveValidationError("archive member path is too long")
    return canonical, parts


def inspect_archive(path: Path) -> list[AcceptedMember]:
    """Return accepted metadata only after validating the complete archive."""

    if path.is_symlink() or not path.is_file():
        raise ArchiveValidationError("plaintext archive is not a regular file")
    archive_stat = path.stat()
    if archive_stat.st_nlink != 1 or not 1 <= archive_stat.st_size <= MAX_ARCHIVE_BYTES:
        raise ArchiveValidationError("plaintext archive size or link count is unsafe")
    if archive_stat.st_size % BLOCK_SIZE != 0:
        raise ArchiveValidationError("plaintext tar is not block aligned")

    accepted: list[AcceptedMember] = []
    names: set[str] = set()
    directories: set[str] = set()
    total_size = 0
    final_payload_end = 0
    try:
        with tarfile.open(path, mode="r:", errorlevel=2) as archive:
            if archive.pax_headers:
                raise ArchiveValidationError("global PAX metadata is forbidden")
            for info in archive:
                if len(accepted) >= MAX_MEMBERS:
                    raise ArchiveValidationError("archive has too many members")
                name, parts = _canonical_name(info.name)
                del parts
                if name in names:
                    raise ArchiveValidationError("archive has a duplicate normalized path")
                names.add(name)
                if info.pax_headers:
                    raise ArchiveValidationError("per-member PAX metadata is forbidden")
                if info.linkname:
                    raise ArchiveValidationError("archive links are forbidden")
                if getattr(info, "sparse", None):
                    raise ArchiveValidationError("sparse archive members are forbidden")
                if info.uid != 0 or info.gid != 0:
                    raise ArchiveValidationError("archive ownership metadata is not root-only")
                mode = info.mode
                if mode < 0 or mode & ~0o777 or mode & 0o022:
                    raise ArchiveValidationError("archive member permissions are unsafe")

                if info.type == tarfile.DIRTYPE:
                    if info.size != 0 or mode & 0o500 != 0o500:
                        raise ArchiveValidationError("archive directory metadata is unsafe")
                    kind = "directory"
                    directories.add(name)
                elif info.type in {tarfile.REGTYPE, tarfile.AREGTYPE}:
                    if mode & 0o111 or mode & 0o400 != 0o400:
                        raise ArchiveValidationError("archive regular-file permissions are unsafe")
                    if not 0 <= info.size <= MAX_MEMBER_BYTES:
                        raise ArchiveValidationError("archive member exceeds the individual size limit")
                    total_size += info.size
                    if total_size > MAX_TOTAL_FILE_BYTES:
                        raise ArchiveValidationError("archive exceeds the total payload limit")
                    kind = "file"
                else:
                    raise ArchiveValidationError("archive contains a link or special member")

                data_end = info.offset_data + info.size
                # USTAR members have one header block immediately before their
                # data. A gap signals GNU long-name/PAX/other extension records
                # that are outside this deliberately narrow archive format.
                if info.offset < 0 or info.offset_data != info.offset + BLOCK_SIZE or data_end > archive_stat.st_size:
                    raise ArchiveValidationError("archive member offsets are invalid")
                final_payload_end = max(final_payload_end, _round_block(data_end))
                accepted.append(AcceptedMember(name, kind, mode, info.size, info))
    except (tarfile.TarError, OSError, OverflowError, ValueError) as exc:
        if isinstance(exc, ArchiveValidationError):
            raise
        raise ArchiveValidationError("plaintext tar metadata is invalid") from exc

    if not accepted or "edcore-automation" not in directories:
        raise ArchiveValidationError("archive lacks the fixed root directory")
    for member in accepted:
        parts = member.name.split("/")
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            if parent not in directories:
                raise ArchiveValidationError("archive member parent is not an explicit directory")

    # Reject hidden members or arbitrary data after an early end-of-archive
    # marker. A valid GNU/POSIX tar has at least two trailing zero blocks.
    if archive_stat.st_size - final_payload_end < 2 * BLOCK_SIZE:
        raise ArchiveValidationError("plaintext tar lacks a complete end marker")
    with path.open("rb") as handle:
        handle.seek(final_payload_end)
        while chunk := handle.read(1024 * 1024):
            if any(chunk):
                raise ArchiveValidationError("plaintext tar has non-zero trailing data")
    return accepted


def _copy_exact(source: BinaryIO, destination_fd: int, size: int) -> None:
    remaining = size
    while remaining:
        chunk = source.read(min(1024 * 1024, remaining))
        if not chunk:
            raise ArchiveValidationError("archive member data is truncated")
        view = memoryview(chunk)
        while view:
            written = os.write(destination_fd, view)
            if written <= 0:
                raise ArchiveValidationError("unable to write extracted member")
            view = view[written:]
        remaining -= len(chunk)
    if source.read(1):
        raise ArchiveValidationError("archive member exceeds its declared size")


def extract_accepted(path: Path, destination: Path, members: list[AcceptedMember]) -> None:
    """Extract previously accepted members beneath a new private directory."""

    if destination.exists() or destination.is_symlink():
        raise ArchiveValidationError("extraction destination already exists")
    destination.mkdir(mode=0o700, parents=False)
    base = destination.resolve(strict=True)

    directories = sorted(
        (member for member in members if member.kind == "directory"),
        key=lambda item: (item.name.count("/"), item.name),
    )
    files = [member for member in members if member.kind == "file"]
    for member in directories:
        target = destination.joinpath(*member.name.split("/"))
        if os.path.commonpath((str(base), str(target.absolute()))) != str(base):
            raise ArchiveValidationError("directory extraction escaped destination")
        target.mkdir(mode=0o700, parents=False, exist_ok=False)

    try:
        with tarfile.open(path, mode="r:", errorlevel=2) as archive:
            by_offset = {info.offset: info for info in archive}
            for member in files:
                info = by_offset.get(member.info.offset)
                if info is None or info.name != member.info.name or info.size != member.size:
                    raise ArchiveValidationError("archive metadata changed before extraction")
                target = destination.joinpath(*member.name.split("/"))
                if os.path.commonpath((str(base), str(target.absolute()))) != str(base):
                    raise ArchiveValidationError("file extraction escaped destination")
                source = archive.extractfile(info)
                if source is None:
                    raise ArchiveValidationError("regular archive member has no data")
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(target, flags, 0o600)
                try:
                    with source:
                        _copy_exact(source, descriptor, member.size)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.chmod(target, member.mode, follow_symlinks=False)
    except (tarfile.TarError, OSError, OverflowError, ValueError) as exc:
        if isinstance(exc, ArchiveValidationError):
            raise
        raise ArchiveValidationError("safe archive extraction failed") from exc

    for member in sorted(directories, key=lambda item: item.name.count("/"), reverse=True):
        target = destination.joinpath(*member.name.split("/"))
        os.chmod(target, member.mode, follow_symlinks=False)
    for target in destination.rglob("*"):
        metadata = target.lstat()
        if stat.S_ISREG(metadata.st_mode):
            if metadata.st_nlink != 1:
                raise ArchiveValidationError("extracted file has an unsafe link count")
        elif not stat.S_ISDIR(metadata.st_mode):
            raise ArchiveValidationError("extraction produced a non-regular member")


def inspect_and_extract(path: Path, destination: Path) -> None:
    members = inspect_archive(path)
    extract_accepted(path, destination, members)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: secret_escrow_archive.py PLAINTEXT_TAR NEW_DESTINATION")
    try:
        inspect_and_extract(Path(sys.argv[1]), Path(sys.argv[2]))
    except ArchiveValidationError as exc:
        print(f"secret escrow archive rejected: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
