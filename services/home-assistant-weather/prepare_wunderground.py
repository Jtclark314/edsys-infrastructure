#!/usr/bin/env python3
"""Prepare the pinned public integration; never connects to Home Assistant."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tempfile
import urllib.request
import zipfile
from io import BytesIO

ROOT = Path(__file__).resolve().parent
COMPONENT = Path("custom_components/wundergroundpws")
MAX_ARCHIVE_BYTES = 10 * 1024 * 1024


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def manifest(component):
    """Hash every package file, rejecting symlinks and unexpected runtime files."""
    rows = []
    for path in sorted(component.rglob("*")):
        if path.is_symlink():
            raise ValueError("Source package contains a symlink")
        if path.is_file():
            rows.append(f"{sha256(path.read_bytes())}  {path.relative_to(component).as_posix()}\n")
    return "".join(rows)


def extract_archive(data, destination):
    """Extract only the component from the verified public source archive."""
    seen = set()
    with zipfile.ZipFile(BytesIO(data)) as archive:
        if sum(item.file_size for item in archive.infolist()) > MAX_ARCHIVE_BYTES:
            raise ValueError("Public archive is unexpectedly large")
        for item in archive.infolist():
            path = PurePosixPath(item.filename)
            if (path.is_absolute() or ".." in path.parts or "\\" in item.filename
                    or stat.S_ISLNK(item.external_attr >> 16) or item.filename in seen):
                raise ValueError("Unsafe or duplicate public archive member")
            seen.add(item.filename)
            relative = Path(*path.parts[1:])
            if item.is_dir() or not relative.is_relative_to(COMPONENT):
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(item))


def prepare(source=None, output=None, pins_path=None):
    pins = json.loads((pins_path or ROOT / "pins.json").read_text())
    patch = ROOT / pins["patch"]
    if sha256(patch.read_bytes()) != pins["patch_sha256"]:
        raise ValueError("Patch hash does not match pins.json")
    if not shutil.which("patch"):
        raise ValueError("The standard patch command is required")
    if output is None:
        output = Path(tempfile.mkdtemp(prefix="edsys-wundergroundpws-prepared-"))
    else:
        output = Path(output).absolute()
        output.mkdir(mode=0o700, parents=False, exist_ok=False)
    output.chmod(0o700)
    try:
        component = output / COMPONENT
        if source is not None:
            original = Path(source) / COMPONENT
            # Validate before copytree, which would otherwise follow symlinks.
            if sha256(manifest(original).encode()) != pins["component_tree_sha256"]:
                raise ValueError("Upstream source tree hash does not match the pin")
            shutil.copytree(original, component)
        else:
            with urllib.request.urlopen(pins["archive_url"], timeout=30) as response:
                data = response.read(MAX_ARCHIVE_BYTES + 1)
            if len(data) > MAX_ARCHIVE_BYTES or sha256(data) != pins["archive_sha256"]:
                raise ValueError("Upstream archive hash does not match the pin")
            extract_archive(data, output)
        if sha256(manifest(component).encode()) != pins["component_tree_sha256"]:
            raise ValueError("Upstream component tree hash does not match the pin")
        for name, expected in pins["upstream_files"].items():
            if sha256((component / name).read_bytes()) != expected:
                raise ValueError(f"Upstream file hash does not match the pin: {name}")
        result = subprocess.run(
            ["patch", "--batch", "--forward", "--fuzz=0", "--no-backup-if-mismatch",
             "-p1", "-i", str(patch)], cwd=output, capture_output=True, text=True,
            timeout=30, check=False,
        )
        if result.returncode or "offset" in result.stdout.lower():
            raise ValueError("Pinned patch did not apply exactly")
        text = manifest(component)
        digest_file = output / "SHA256SUMS"
        digest_file.write_text(text)
        for path in output.rglob("*"):
            path.chmod(0o700 if path.is_dir() else 0o600)
        return {
            "version": pins["version"], "upstream_commit": pins["commit"],
            "prepared_component": str(component), "manifest": str(digest_file),
            "manifest_sha256": sha256(text.encode()), "patch_sha256": pins["patch_sha256"],
            "files": len(text.splitlines()),
        }
    except BaseException:
        shutil.rmtree(output)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Offline upstream checkout containing custom_components")
    parser.add_argument("--output", type=Path, help="New private output directory; existing paths are refused")
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
