"""Adversarial tests for the pure secret-escrow tar inspector/extractor."""

from __future__ import annotations

import ast
import io
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
from typing import Callable
import unittest


STACK_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = STACK_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from secret_escrow_archive import (  # noqa: E402
    ArchiveValidationError,
    MAX_ARCHIVE_BYTES,
    MAX_MEMBER_BYTES,
    MAX_MEMBERS,
    MAX_TOTAL_FILE_BYTES,
    inspect_and_extract,
    inspect_archive,
)


def member(
    name: str,
    *,
    kind: bytes = tarfile.REGTYPE,
    mode: int | None = None,
    payload: bytes = b"",
    size: int | None = None,
    uid: int = 0,
    gid: int = 0,
    linkname: str = "",
    pax_headers: dict[str, str] | None = None,
) -> tuple[tarfile.TarInfo, bytes | None]:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.mode = mode if mode is not None else (0o700 if kind == tarfile.DIRTYPE else 0o400)
    info.uid = uid
    info.gid = gid
    info.uname = "root"
    info.gname = "root"
    info.linkname = linkname
    info.pax_headers = pax_headers or {}
    if kind in {tarfile.REGTYPE, tarfile.AREGTYPE}:
        info.size = len(payload) if size is None else size
        return info, payload if size is None else None
    info.size = 0 if size is None else size
    return info, None


def write_archive(
    path: Path,
    members: list[tuple[tarfile.TarInfo, bytes | None]],
    *,
    pax_headers: dict[str, str] | None = None,
) -> None:
    archive_format = (
        tarfile.PAX_FORMAT
        if pax_headers is not None or any(info.pax_headers for info, _ in members)
        else tarfile.USTAR_FORMAT
    )
    with tarfile.open(path, mode="w", format=archive_format, pax_headers=pax_headers) as archive:
        for info, payload in members:
            if info.isreg():
                if payload is None:
                    payload = b"x" * info.size
                archive.addfile(info, io.BytesIO(payload))
            else:
                archive.addfile(info)


def safe_members() -> list[tuple[tarfile.TarInfo, bytes | None]]:
    return [
        member("edcore-automation", kind=tarfile.DIRTYPE, mode=0o700),
        member("edcore-automation/pki", kind=tarfile.DIRTYPE, mode=0o750),
        member("edcore-automation/pki/ca.crt", mode=0o440, payload=b"public-test-data\n"),
    ]


class SafeArchiveTestCase(unittest.TestCase):
    def assert_rejected(
        self,
        members: list[tuple[tarfile.TarInfo, bytes | None]],
        *,
        pax_headers: dict[str, str] | None = None,
        mutate: Callable[[Path], None] | None = None,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.tar"
            destination = root / "extracted"
            write_archive(archive, members, pax_headers=pax_headers)
            if mutate is not None:
                mutate(archive)
            with self.assertRaises(ArchiveValidationError):
                inspect_and_extract(archive, destination)
            self.assertFalse(destination.exists(), "inspection failure created an extraction destination")

    def test_safe_archive_is_fully_inspected_then_manually_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.tar"
            destination = root / "extracted"
            write_archive(archive, safe_members())

            accepted = inspect_archive(archive)
            self.assertFalse(destination.exists())
            self.assertEqual([item.name for item in accepted], [
                "edcore-automation",
                "edcore-automation/pki",
                "edcore-automation/pki/ca.crt",
            ])
            inspect_and_extract(archive, destination)

            restored = destination / "edcore-automation" / "pki" / "ca.crt"
            self.assertEqual(restored.read_bytes(), b"public-test-data\n")
            self.assertEqual(stat.S_IMODE(restored.stat().st_mode), 0o440)
            self.assertEqual(restored.stat().st_nlink, 1)
            self.assertFalse(any(path.is_symlink() for path in destination.rglob("*")))

    def test_absolute_traversal_and_ambiguous_paths_are_rejected(self) -> None:
        bad_names = (
            "/edcore-automation/file",
            "edcore-automation/../escape",
            "edcore-automation/./file",
            "edcore-automation//file",
            r"edcore-automation\file",
            "wrong-root/file",
        )
        for name in bad_names:
            with self.subTest(name=name):
                self.assert_rejected([
                    member("edcore-automation", kind=tarfile.DIRTYPE),
                    member(name, payload=b"x"),
                ])

    def test_duplicate_normalized_paths_are_rejected(self) -> None:
        self.assert_rejected([
            member("edcore-automation", kind=tarfile.DIRTYPE),
            member("edcore-automation/value", payload=b"one"),
            member("edcore-automation/value", payload=b"two"),
        ])

    def test_links_devices_fifo_socket_and_unknown_specials_are_rejected(self) -> None:
        special_members = (
            member("edcore-automation/link", kind=tarfile.SYMTYPE, linkname="target"),
            member("edcore-automation/link", kind=tarfile.LNKTYPE, linkname="edcore-automation/value"),
            member("edcore-automation/device", kind=tarfile.CHRTYPE),
            member("edcore-automation/device", kind=tarfile.BLKTYPE),
            member("edcore-automation/fifo", kind=tarfile.FIFOTYPE),
            member("edcore-automation/socket", kind=b"S"),
        )
        for special in special_members:
            with self.subTest(kind=special[0].type):
                self.assert_rejected([
                    member("edcore-automation", kind=tarfile.DIRTYPE),
                    special,
                ])

    def test_global_and_per_member_pax_metadata_are_rejected(self) -> None:
        self.assert_rejected(safe_members(), pax_headers={"comment": "unexpected"})
        self.assert_rejected([
            member("edcore-automation", kind=tarfile.DIRTYPE),
            member(
                "edcore-automation/value",
                payload=b"x",
                pax_headers={"comment": "unexpected"},
            ),
        ])

    def test_unsafe_ownership_modes_and_missing_explicit_parent_are_rejected(self) -> None:
        cases = (
            [member("edcore-automation", kind=tarfile.DIRTYPE, uid=1)],
            [member("edcore-automation", kind=tarfile.DIRTYPE, mode=0o720)],
            [
                member("edcore-automation", kind=tarfile.DIRTYPE),
                member("edcore-automation/value", mode=0o500, payload=b"x"),
            ],
            [
                member("edcore-automation", kind=tarfile.DIRTYPE),
                member("edcore-automation/missing/value", payload=b"x"),
            ],
        )
        for index, members in enumerate(cases):
            with self.subTest(case=index):
                self.assert_rejected(members)

    def test_member_count_individual_and_total_size_limits_are_enforced(self) -> None:
        too_many = [member("edcore-automation", kind=tarfile.DIRTYPE)]
        too_many.extend(
            member(f"edcore-automation/f{index:04d}")
            for index in range(MAX_MEMBERS)
        )
        self.assert_rejected(too_many)

        self.assert_rejected([
            member("edcore-automation", kind=tarfile.DIRTYPE),
            member("edcore-automation/oversize", size=MAX_MEMBER_BYTES + 1),
        ])

        file_count = MAX_TOTAL_FILE_BYTES // MAX_MEMBER_BYTES + 1
        self.assert_rejected([
            member("edcore-automation", kind=tarfile.DIRTYPE),
            *(
                member(f"edcore-automation/large{index}", size=MAX_MEMBER_BYTES)
                for index in range(file_count)
            ),
        ])

    def test_archive_size_alignment_and_nonzero_trailing_data_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            oversized = root / "oversized.tar"
            with oversized.open("wb") as handle:
                handle.truncate(MAX_ARCHIVE_BYTES + 512)
            with self.assertRaises(ArchiveValidationError):
                inspect_archive(oversized)

        self.assert_rejected(safe_members(), mutate=lambda path: path.write_bytes(path.read_bytes() + b"x"))
        self.assert_rejected(
            safe_members(),
            mutate=lambda path: path.write_bytes(path.read_bytes() + (b"x" * 512)),
        )

    def test_helper_never_calls_tarfile_extract_apis_and_uses_nofollow_exclusive_create(self) -> None:
        source_path = SCRIPTS / "secret_escrow_archive.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        called_attributes = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertNotIn("extract", called_attributes)
        self.assertNotIn("extractall", called_attributes)
        self.assertIn("os.O_EXCL", source)
        self.assertIn('hasattr(os, "O_NOFOLLOW")', source)
        self.assertIn("flags |= os.O_NOFOLLOW", source)
        self.assertIn('getattr(info, "sparse", None)', source)
        self.assertEqual(source_path.stat().st_mode & 0o777, 0o644)
        self.assertFalse(source.startswith("#!"))

    def test_public_limits_are_exact(self) -> None:
        self.assertEqual(MAX_ARCHIVE_BYTES, 32 * 1024 * 1024)
        self.assertEqual(MAX_MEMBERS, 512)
        self.assertEqual(MAX_MEMBER_BYTES, 4 * 1024 * 1024)
        self.assertEqual(MAX_TOTAL_FILE_BYTES, 16 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
