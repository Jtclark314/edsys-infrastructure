import importlib.util
import os
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "validate-installed-pull.py"
SPEC = importlib.util.spec_from_file_location("validate_installed_pull", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def installed_tree(tmp_path: Path) -> tuple[Path, Path]:
    libexec = tmp_path / "usr" / "local" / "libexec" / "edsys-edcore-automation"
    units = tmp_path / "etc" / "systemd" / "system"
    libexec.mkdir(parents=True)
    units.mkdir(parents=True)
    for directory in (
        tmp_path,
        tmp_path / "usr",
        tmp_path / "usr" / "local",
        tmp_path / "usr" / "local" / "libexec",
        libexec,
        tmp_path / "etc",
        tmp_path / "etc" / "systemd",
        units,
    ):
        directory.chmod(0o755)
    for name, mode in MODULE.LIBEXEC_FILES.items():
        path = libexec / name
        path.write_text("installed\n", encoding="utf-8")
        path.chmod(mode)
    for name, mode in MODULE.UNIT_FILES.items():
        path = units / name
        path.write_text("installed\n", encoding="utf-8")
        path.chmod(mode)
    return libexec, units


def validate(tmp_path: Path, libexec: Path, units: Path) -> None:
    MODULE.validate_install(
        libexec,
        units,
        expected_uid=os.getuid(),
        expected_gid=os.getgid(),
        libexec_floor=tmp_path,
        unit_floor=tmp_path,
    )


def test_accepts_exact_root_copy_contract(tmp_path):
    libexec, units = installed_tree(tmp_path)
    validate(tmp_path, libexec, units)


def test_rejects_mutable_or_linked_program_files(tmp_path):
    libexec, units = installed_tree(tmp_path)
    pull = libexec / "pull-backup.sh"
    pull.chmod(0o775)
    with pytest.raises(MODULE.InstallValidationError, match="mode mismatch"):
        validate(tmp_path, libexec, units)

    pull.unlink()
    pull.symlink_to(libexec / "verify-backup.py")
    with pytest.raises(MODULE.InstallValidationError, match="regular file"):
        validate(tmp_path, libexec, units)


def test_rejects_a_group_writable_path_component(tmp_path):
    libexec, units = installed_tree(tmp_path)
    (tmp_path / "usr" / "local").chmod(0o775)
    with pytest.raises(MODULE.InstallValidationError, match="group/world writable"):
        validate(tmp_path, libexec, units)


def test_rejects_wrong_unit_mode(tmp_path):
    libexec, units = installed_tree(tmp_path)
    (units / "edsys-edcore-automation-backup-pull.service").chmod(0o664)
    with pytest.raises(MODULE.InstallValidationError, match="mode mismatch"):
        validate(tmp_path, libexec, units)
