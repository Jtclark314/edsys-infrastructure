#!/usr/bin/env python3
"""Root-side forced-command export gate for immutable automation backups."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


BACKUP_ROOT = Path("/var/backups/edcore-automation")
VERIFY_PROGRAM = Path("/usr/local/libexec/edsys-edcore-automation-backup-verify")
RUN_ID_RE = re.compile(r"^20[0-9]{6}T[0-9]{6}Z$")


class ExportError(RuntimeError):
    """The forced command or selected backup is unsafe."""


def require_root_controlled_directory(path: Path, trusted_uid: int = 0) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
        raise ExportError(f"backup path is not a real directory: {path}")
    if info.st_uid != trusted_uid or stat.S_IMODE(info.st_mode) & 0o022:
        raise ExportError(f"backup directory is not root-controlled: {path}")


def validate_tree(root: Path, trusted_uid: int = 0) -> None:
    required = {"MANIFEST.json", "SHA256SUMS"}
    found_root_files: set[str] = set()
    for directory, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        base = Path(directory)
        require_root_controlled_directory(base, trusted_uid)
        for name in dirnames:
            candidate = base / name
            require_root_controlled_directory(candidate, trusted_uid)
        for name in filenames:
            candidate = base / name
            info = candidate.lstat()
            if not stat.S_ISREG(info.st_mode) or candidate.is_symlink():
                raise ExportError("backup contains a file link or special entry")
            if info.st_uid != trusted_uid or stat.S_IMODE(info.st_mode) & 0o022:
                raise ExportError("backup contains a file that is not root-controlled")
            if base == root:
                found_root_files.add(name)
    if not required.issubset(found_root_files):
        raise ExportError("backup metadata is incomplete")


def resolve_current(
    backup_root: Path,
    trusted_uid: int = 0,
    control_floor: Path = Path("/"),
) -> Path:
    root = backup_root.absolute()
    floor = control_floor.absolute()
    if root != floor and floor not in root.parents:
        raise ExportError("backup root is outside its root-control validation floor")
    current_component = floor
    require_root_controlled_directory(current_component, trusted_uid)
    for part in root.relative_to(floor).parts:
        current_component /= part
        require_root_controlled_directory(current_component, trusted_uid)
    current = root / "current"
    if not current.is_symlink():
        raise ExportError("current is not a symlink")
    selected = current.resolve(strict=True)
    if selected.parent != root or not RUN_ID_RE.fullmatch(selected.name):
        raise ExportError("current resolves outside the immutable run namespace")
    if selected.is_symlink() or not selected.is_dir():
        raise ExportError("current target is not a regular run directory")
    validate_tree(selected, trusted_uid)
    return selected


def verify_current_backup(selected: Path, verify_program: Path = VERIFY_PROGRAM) -> None:
    info = verify_program.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or verify_program.is_symlink()
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) & 0o022
        or not os.access(verify_program, os.X_OK)
    ):
        raise ExportError("installed backup verifier is not root-controlled and executable")
    try:
        subprocess.run(
            [str(verify_program), str(selected), "--expected-run-id", selected.name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ExportError("current backup failed manifest/hash verification") from exc


def parse_forced_command(original: str) -> tuple[str, str | None]:
    if original == "edsys-backup-current":
        return "current", None
    match = re.fullmatch(r"edsys-backup-export (20[0-9]{6}T[0-9]{6}Z)", original)
    if match:
        return "export", match.group(1)
    raise ExportError("forced command is not permitted")


def execute(
    original: str,
    backup_root: Path = BACKUP_ROOT,
    trusted_uid: int = 0,
    control_floor: Path = Path("/"),
) -> None:
    action, requested_run = parse_forced_command(original)
    selected = resolve_current(backup_root, trusted_uid, control_floor)
    if action == "current":
        print(selected.name)
        return
    if requested_run != selected.name:
        raise ExportError("only the current immutable run may be exported")
    verify_current_backup(selected)
    os.execve(
        "/bin/tar",
        ["tar", "--create", "--file=-", "--directory", str(selected), "."],
        {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original_command", help="One SSH_ORIGINAL_COMMAND string from the launcher")
    return parser.parse_args()


def main() -> int:
    if os.geteuid() != 0:
        raise ExportError("forced-command export gate must run as root")
    args = parse_args()
    execute(args.original_command)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ExportError, OSError) as exc:
        print(f"backup export refused: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
