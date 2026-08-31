from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "edcore-control.py"
SPEC = importlib.util.spec_from_file_location("edcore_control", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_current_proxmox_defaults() -> None:
    assert MODULE.DEFAULT_HOST == "pve-node3"
    assert MODULE.HA_VMID == 300
    assert MODULE.KALI_VMID == 330
    assert MODULE.TARGET_VMID == 331


def test_kali_ssh_is_key_only_and_jumps_through_node(monkeypatch, tmp_path: Path) -> None:
    key = tmp_path / "kali"
    key.write_text("not-a-real-key\n", encoding="utf-8")
    monkeypatch.setattr(MODULE, "KALI_LAB_KEY", key)

    argv = MODULE.kali_ssh_argv("pve-node3")

    assert argv[:3] == ["ssh", "-J", "pve-node3"]
    assert "BatchMode=yes" in argv
    assert "IdentitiesOnly=yes" in argv
    assert "PasswordAuthentication=no" in argv
    assert argv[-1] == "kali@192.168.77.10"


def test_parser_routes_expected_current_commands() -> None:
    parser = MODULE.build_parser()

    assert parser.parse_args(["status"]).action == "status"
    assert parser.parse_args(["isolation"]).action == "isolation"
    assert parser.parse_args(["ha", "status"]).ha_command == "status"
    assert parser.parse_args(["lab", "restore-starter"]).lab_command == "restore-starter"
    assert parser.parse_args(["target", "restore-clean"]).target_command == "restore-clean"
