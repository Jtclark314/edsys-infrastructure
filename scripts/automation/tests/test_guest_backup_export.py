import importlib.util
import os
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "guest-backup-export.py"
SPEC = importlib.util.spec_from_file_location("guest_backup_export", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

RUN_ID = "20260822T120000Z"


def backup_tree(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "backups"
    root.mkdir(mode=0o700)
    run = root / RUN_ID
    run.mkdir(mode=0o700)
    for name, payload in {
        "MANIFEST.json": "{}\n",
        "SHA256SUMS": "placeholder\n",
        "payload.txt": "safe\n",
    }.items():
        path = run / name
        path.write_text(payload, encoding="utf-8")
        path.chmod(0o600)
    (root / "current").symlink_to(RUN_ID)
    return root, run


def test_forced_command_parser_rejects_shell_and_transport_commands():
    assert MODULE.parse_forced_command("edsys-backup-current") == ("current", None)
    assert MODULE.parse_forced_command(f"edsys-backup-export {RUN_ID}") == (
        "export",
        RUN_ID,
    )
    for command in (
        "",
        "sh",
        "uname -a",
        "edsys-backup-current; id",
        "rsync --server . /",
        "sftp",
        f"edsys-backup-export {RUN_ID} extra",
    ):
        with pytest.raises(MODULE.ExportError):
            MODULE.parse_forced_command(command)


def test_current_returns_only_the_validated_run_id(tmp_path, capsys):
    root, _ = backup_tree(tmp_path)
    MODULE.execute(
        "edsys-backup-current",
        root,
        trusted_uid=os.getuid(),
        control_floor=root,
    )
    assert capsys.readouterr().out == f"{RUN_ID}\n"


def test_export_is_limited_to_current_and_fixed_tar_invocation(tmp_path, monkeypatch):
    root, run = backup_tree(tmp_path)
    verified = []
    invoked = []
    monkeypatch.setattr(MODULE, "verify_current_backup", lambda selected: verified.append(selected))
    monkeypatch.setattr(
        MODULE.os,
        "execve",
        lambda path, args, env: invoked.append((path, args, env)),
    )

    MODULE.execute(
        f"edsys-backup-export {RUN_ID}",
        root,
        trusted_uid=os.getuid(),
        control_floor=root,
    )

    assert verified == [run]
    assert invoked == [
        (
            "/bin/tar",
            ["tar", "--create", "--file=-", "--directory", str(run), "."],
            {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
    ]


def test_rejects_noncurrent_outside_and_mutable_trees(tmp_path):
    root, run = backup_tree(tmp_path)
    with pytest.raises(MODULE.ExportError, match="only the current"):
        MODULE.execute(
            "edsys-backup-export 20260821T120000Z",
            root,
            trusted_uid=os.getuid(),
            control_floor=root,
        )

    (root / "current").unlink()
    outside = tmp_path / "outside" / RUN_ID
    outside.mkdir(parents=True)
    (root / "current").symlink_to(outside)
    with pytest.raises(MODULE.ExportError, match="outside"):
        MODULE.resolve_current(root, os.getuid(), root)

    (root / "current").unlink()
    (root / "current").symlink_to(RUN_ID)
    run.chmod(0o770)
    with pytest.raises(MODULE.ExportError, match="root-controlled"):
        MODULE.resolve_current(root, os.getuid(), root)


def test_rejects_symlinks_inside_the_selected_tree(tmp_path):
    root, run = backup_tree(tmp_path)
    (run / "link").symlink_to("/etc/passwd")
    with pytest.raises(MODULE.ExportError, match="link or special"):
        MODULE.resolve_current(root, os.getuid(), root)
