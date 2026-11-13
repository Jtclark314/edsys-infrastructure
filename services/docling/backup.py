#!/usr/bin/python3
"""Stage owned Docling outputs, exact model artifacts and reconstruction metadata."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

RUNTIME = Path("/home/jeremy/.local/share/edsys-docling")
MODELS = Path("/mnt/ai-store/models/docling")
STAGE = Path("/srv/edsys-backup/staging/docling")


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def verify(directory: Path) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        path = directory / name
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
            raise RuntimeError("Invalid restore member")
        if (
            sha256(path) != expected["sha256"]
            or path.stat().st_size != expected["bytes"]
        ):
            raise RuntimeError("Restored file integrity mismatch")
    return {
        "verified_files": len(manifest["files"]),
        "bytes": sum(x["bytes"] for x in manifest["files"].values()),
    }


def stage() -> dict:
    os.umask(0o077)
    if os.geteuid() != 0 or not Path("/mnt/ai-store").is_mount():
        raise SystemExit("Run as root with AI Store mounted")
    with (RUNTIME / "state/job.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        install = json.loads((RUNTIME / "install.json").read_text())
        models = json.loads((MODELS / "models.json").read_text())
        selected = {
            "runtime/install.json": RUNTIME / "install.json",
            "runtime/.edsys-owner": RUNTIME / ".edsys-owner",
            "models/models.json": MODELS / "models.json",
            "models/.edsys-owner": MODELS / ".edsys-owner",
        }
        selected["runtime/bin/edsys-docling"] = RUNTIME / "bin/edsys-docling"
        release = Path(install["release"])
        if not release.resolve().is_relative_to((RUNTIME / "releases").resolve()):
            raise RuntimeError("Release is outside owned runtime")
        for path in release.iterdir():
            selected[str(Path("runtime") / path.relative_to(RUNTIME))] = path
        for relative in models["files"]:
            selected[str(Path("models") / relative)] = MODELS / relative
        for path in (RUNTIME / "outputs").rglob("*"):
            if path.is_file() or path.is_symlink():
                selected[str(Path("runtime") / path.relative_to(RUNTIME))] = path
        STAGE.mkdir(parents=True, mode=0o700, exist_ok=True)
        directory = STAGE / (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + uuid.uuid4().hex[:8]
        )
        directory.mkdir(mode=0o700)
        files = {}
        for relative, source in selected.items():
            if source.is_symlink() or not source.is_file():
                raise RuntimeError("Unexpected symlink or non-file in backup source")
            target = directory / relative
            target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o600)
            files[relative] = {"sha256": sha256(target), "bytes": target.stat().st_size}
        (directory / "manifest.json").write_text(
            json.dumps({"schema": 1, "files": files}, indent=2, sort_keys=True) + "\n"
        )
        result = verify(directory)
        temporary = STAGE / (".current-" + uuid.uuid4().hex)
        temporary.symlink_to(directory.name)
        os.replace(temporary, STAGE / "current")
        # Staging is a handoff to versioned encrypted backups, not another archive.
        # Keep the current and immediately previous verified staging directories.
        completed = sorted(
            (
                p
                for p in STAGE.iterdir()
                if p.is_dir() and not p.is_symlink() and (p / "manifest.json").is_file()
            ),
            key=lambda p: p.name,
            reverse=True,
        )
        for previous in completed[2:]:
            verify(previous)
            shutil.rmtree(previous)
        return {"stage": str(directory), **result}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.verify.resolve()) if args.verify else stage()))
