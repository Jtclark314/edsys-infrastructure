from __future__ import annotations

import json
from pathlib import Path

from edsys_fleet.config import FleetConfig
from edsys_fleet.proxmox import ProxmoxClient, ProxmoxError
from edsys_fleet.runner import CommandResult


class FakeRunner:
    def __init__(self):
        self.commands: list[str] = []

    def ssh(self, alias: str, command: str, timeout=None):
        self.commands.append(command)
        return CommandResult(True, json.dumps([{"name": "pve-node0", "online": 1}]), "", 0, 1)


def config() -> FleetConfig:
    return FleetConfig(
        raw={
            "schema_version": 1,
            "state_root": "/tmp/fleet",
            "proxmox": {"control_alias": "pve-node0", "nodes": ["pve-node0"]},
        },
        path=Path("test.yml"),
    )


def test_cluster_uses_official_pvesh_json_contract():
    runner = FakeRunner()
    value = ProxmoxClient(config(), runner).cluster_status()
    assert value[0]["online"] == 1
    assert runner.commands == ["pvesh get /cluster/status --output-format json"]


def test_task_reference_extracts_upid_from_pvesh_warning_string():
    value = (
        'WARN: Systemd 257 detected. You may need to enable nesting.'
        '"UPID:pve-edcore:001CF2B4:02220817:6A75534E:vzstart:390:root@pam:"'
    )
    assert ProxmoxClient._task_reference(value) == (
        "UPID:pve-edcore:001CF2B4:02220817:6A75534E:vzstart:390:root@pam:"
    )


def test_guest_target_validation_rejects_unknown_node():
    try:
        ProxmoxClient(config(), FakeRunner()).guest_status("unknown", 321)
    except ProxmoxError as exc:
        assert "Unknown Proxmox node" in str(exc)
    else:
        raise AssertionError("unknown node was not rejected")


def test_snapshot_name_is_bounded():
    try:
        ProxmoxClient(config(), FakeRunner()).create_snapshot("pve-node0", 321, "unsafe name")
    except ProxmoxError as exc:
        assert "unsafe" in str(exc).lower()
    else:
        raise AssertionError("unsafe snapshot name was not rejected")


def test_snapshot_description_is_shell_quoted_as_one_argument():
    runner = FakeRunner()

    ProxmoxClient(config(), runner).create_snapshot(
        "pve-node0", 321, "fleet-test", "Fleet Autopilot acceptance checkpoint"
    )

    assert runner.commands == [
        "pvesh create /nodes/pve-node0/qemu/321/snapshot --output-format json "
        "--snapname fleet-test --description 'Fleet Autopilot acceptance checkpoint'"
    ]
