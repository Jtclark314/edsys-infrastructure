import io
import importlib.util
from pathlib import Path
import tarfile

import pytest


SCRIPT = Path(__file__).parents[1] / "extract-backup.py"
SPEC = importlib.util.spec_from_file_location("extract_backup", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def make_archive(path: Path, entries: list[tuple[tarfile.TarInfo, bytes]]) -> None:
    with tarfile.open(path, "w") as archive:
        for member, payload in entries:
            archive.addfile(member, io.BytesIO(payload) if member.isreg() else None)


def regular(name: str, payload: bytes) -> tuple[tarfile.TarInfo, bytes]:
    member = tarfile.TarInfo(name)
    member.size = len(payload)
    member.mode = 0o600
    return member, payload


def test_extracts_only_regular_files_and_directories(tmp_path):
    archive = tmp_path / "valid.tar"
    root = tarfile.TarInfo(".")
    root.type = tarfile.DIRTYPE
    root.mode = 0o700
    directory = tarfile.TarInfo("./nested")
    directory.type = tarfile.DIRTYPE
    directory.mode = 0o700
    make_archive(
        archive,
        [
            (root, b""),
            (directory, b""),
            regular("./MANIFEST.json", b"{}\n"),
            regular("./nested/payload", b"safe\n"),
        ],
    )
    destination = tmp_path / "destination"
    destination.mkdir()

    assert MODULE.extract_archive(archive, destination) == 3
    assert (destination / "nested" / "payload").read_bytes() == b"safe\n"


@pytest.mark.parametrize("unsafe_name", ["../escape", "/absolute", "a/../escape"])
def test_rejects_path_traversal(tmp_path, unsafe_name):
    archive = tmp_path / "unsafe.tar"
    make_archive(archive, [regular(unsafe_name, b"unsafe")])
    destination = tmp_path / "destination"
    destination.mkdir()

    with pytest.raises(MODULE.ExtractionError):
        MODULE.extract_archive(archive, destination)


def test_rejects_links_and_duplicate_paths(tmp_path):
    archive = tmp_path / "link.tar"
    link = tarfile.TarInfo("payload-link")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    make_archive(archive, [(link, b"")])
    destination = tmp_path / "destination"
    destination.mkdir()
    with pytest.raises(MODULE.ExtractionError):
        MODULE.extract_archive(archive, destination)

    duplicate_archive = tmp_path / "duplicate.tar"
    make_archive(
        duplicate_archive,
        [regular("payload", b"one"), regular("./payload", b"two")],
    )
    with pytest.raises(MODULE.ExtractionError):
        MODULE.extract_archive(duplicate_archive, destination)


def test_requires_an_empty_real_destination(tmp_path):
    archive = tmp_path / "valid.tar"
    make_archive(archive, [regular("payload", b"safe")])
    destination = tmp_path / "destination"
    destination.mkdir()
    (destination / "already-there").write_text("no", encoding="utf-8")
    with pytest.raises(MODULE.ExtractionError):
        MODULE.extract_archive(archive, destination)
