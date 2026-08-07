from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "fleet-self-update.py"
SPEC = importlib.util.spec_from_file_location("fleet_self_update", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_checkpoint_restores_exact_symlink(tmp_path: Path):
    link = tmp_path / "bin" / "edsys-fleet"
    link.parent.mkdir()
    link.symlink_to("/previous/fleet")
    backup = tmp_path / "backup"
    backup.mkdir()
    record = MODULE.snapshot_path(link, backup, "command")
    MODULE.atomic_symlink("/candidate/fleet", link)
    assert os.readlink(link) == "/candidate/fleet"
    MODULE.restore_path(link, record)
    assert os.readlink(link) == "/previous/fleet"


def test_source_identity_requires_clean_authoritative_main(tmp_path: Path):
    repo = tmp_path / "repo"
    source = repo / "services" / "fleet"
    source.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    (source / "pyproject.toml").write_text("[project]\nname='test'\nversion='1'\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "test"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", "HEAD"], check=True
    )
    identity = MODULE.source_identity(source)
    assert identity["branch"] == "main"
    assert len(identity["archive_sha256"]) == 64
    (source / "pyproject.toml").write_text("dirty\n")
    with pytest.raises(MODULE.UpdateError):
        MODULE.source_identity(source)


def test_candidate_manifest_is_ed25519_signed_and_verified(tmp_path: Path):
    install_root = tmp_path / "install"
    install_root.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"candidate":"abc"}\n')
    result = MODULE.sign_manifest(manifest, install_root)
    assert result["verified"] is True
    assert (install_root / "deployment-signing-key").stat().st_mode & 0o777 == 0o600


def test_checkpoint_digest_changes_with_exact_backup_content(tmp_path: Path):
    backup = tmp_path / "checkpoint"
    backup.mkdir()
    payload = backup / "unit"
    payload.write_text("one", encoding="utf-8")
    record = {"current": {"type": "symlink", "target": "/old/release"}}
    first = MODULE.checkpoint_sha256(record, backup)
    payload.write_text("two", encoding="utf-8")
    second = MODULE.checkpoint_sha256(record, backup)

    assert len(first) == 64
    assert first != second


def test_manual_rollback_remains_available_after_acceptance(tmp_path: Path, monkeypatch):
    install_root = tmp_path / "install"
    bin_dir = tmp_path / "bin"
    unit_dir = tmp_path / "units"
    run_dir = tmp_path / "run"
    checkpoint_dir = run_dir / "checkpoint"
    for path in (install_root, bin_dir, unit_dir, checkpoint_dir):
        path.mkdir(parents=True, exist_ok=True)
    current = install_root / "current"
    current.symlink_to("/candidate/release")
    state = {
        "status": "accepted",
        "paths": {
            "install_root": str(install_root),
            "bin_dir": str(bin_dir),
            "unit_dir": str(unit_dir),
        },
        "checkpoint": {
            "current": {"type": "symlink", "target": "/prior/release"},
            "commands": {},
            "units": {},
        },
    }
    MODULE.write_private_json(run_dir / "state.json", state)
    monkeypatch.setattr(MODULE, "systemd_refresh", lambda **_: None)

    unchanged = MODULE.restore_checkpoint(run_dir)
    assert unchanged["status"] == "accepted"
    restored = MODULE.restore_checkpoint(
        run_dir, terminal_status="rolled_back_manually", allow_accepted=True
    )

    assert restored["status"] == "rolled_back_manually"
    assert os.readlink(current) == "/prior/release"
