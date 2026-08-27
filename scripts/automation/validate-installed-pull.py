#!/usr/bin/env python3
"""Fail closed unless the installed 9950x backup-pull files are root-controlled."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import sys


LIBEXEC_DIR = Path("/usr/local/libexec/edsys-edcore-automation")
UNIT_DIR = Path("/etc/systemd/system")
LIBEXEC_FILES = {
    "pull-backup.sh": 0o755,
    "verify-backup.py": 0o755,
    "extract-backup.py": 0o755,
    "validate-installed-pull.py": 0o755,
}
UNIT_FILES = {
    "edsys-edcore-automation-backup-pull.service": 0o644,
    "edsys-edcore-automation-backup-pull.timer": 0o644,
}


class InstallValidationError(RuntimeError):
    """An installed path is mutable, linked, misowned, or has the wrong mode."""


def path_chain(floor: Path, target: Path) -> list[Path]:
    floor = Path(os.path.abspath(floor))
    target = Path(os.path.abspath(target))
    if target != floor and floor not in target.parents:
        raise InstallValidationError(f"{target} is not beneath validation floor {floor}")
    relative = target.relative_to(floor)
    result = [floor]
    current = floor
    for part in relative.parts:
        current /= part
        result.append(current)
    return result


def require_directory(path: Path, expected_uid: int, expected_gid: int) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise InstallValidationError(f"required directory is missing: {path}") from exc
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
        raise InstallValidationError(f"required path component is not a real directory: {path}")
    if (info.st_uid, info.st_gid) != (expected_uid, expected_gid):
        raise InstallValidationError(f"required directory has the wrong owner: {path}")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise InstallValidationError(f"required directory is group/world writable: {path}")


def require_file(
    path: Path,
    expected_mode: int,
    expected_uid: int,
    expected_gid: int,
) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise InstallValidationError(f"required installed file is missing: {path}") from exc
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise InstallValidationError(f"required installed file is not a regular file: {path}")
    if (info.st_uid, info.st_gid) != (expected_uid, expected_gid):
        raise InstallValidationError(f"installed file has the wrong owner: {path}")
    actual_mode = stat.S_IMODE(info.st_mode)
    if actual_mode != expected_mode:
        raise InstallValidationError(
            f"installed file mode mismatch: {path} is {actual_mode:04o}, expected {expected_mode:04o}"
        )


def validate_install(
    libexec_dir: Path = LIBEXEC_DIR,
    unit_dir: Path = UNIT_DIR,
    *,
    expected_uid: int = 0,
    expected_gid: int = 0,
    libexec_floor: Path = Path("/"),
    unit_floor: Path = Path("/"),
) -> None:
    seen: set[Path] = set()
    for floor, directory in ((libexec_floor, libexec_dir), (unit_floor, unit_dir)):
        for component in path_chain(floor, directory):
            if component not in seen:
                require_directory(component, expected_uid, expected_gid)
                seen.add(component)
    for name, mode in LIBEXEC_FILES.items():
        require_file(libexec_dir / name, mode, expected_uid, expected_gid)
    for name, mode in UNIT_FILES.items():
        require_file(unit_dir / name, mode, expected_uid, expected_gid)


def main() -> int:
    if os.geteuid() != 0:
        raise InstallValidationError("installed backup-pull preflight must run as root")
    validate_install()
    print("PASS installed EdCore Automation backup-pull ownership and modes")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (InstallValidationError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
