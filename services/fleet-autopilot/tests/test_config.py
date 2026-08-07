from pathlib import Path

import yaml

from edsys_fleet.adapters import AdapterRegistry
from edsys_fleet.collector import FleetCollector
from edsys_fleet.config import load_config
from edsys_fleet.runner import CommandResult


def test_operator_policy_and_packaged_policy_are_identical():
    root = Path(__file__).resolve().parents[1]
    assert (root / "config" / "fleet-policy.yml").read_bytes() == (
        root / "edsys_fleet" / "fleet-policy.yml"
    ).read_bytes()


def test_policy_v2_declares_full_lifecycle_and_dell_outbound_agent():
    root = Path(__file__).resolve().parents[1]
    policy = yaml.safe_load((root / "config" / "fleet-policy.yml").read_text())
    assert policy["schema_version"] == 2
    assert policy["policy_version"] == "2.0.0"
    assert policy["hosts"][-1]["transport"] == "signed-outbound-agent"
    assert policy["hosts"][-1]["offline_portable"] is True
    assert policy["proxmox"]["canary"]["vmid"] == 390
    for name, component in policy["components"].items():
        assert component["adapter"], name
        for phase in (
            "discover", "resolve_candidate", "preflight", "checkpoint", "apply",
            "restart_or_reboot", "verify", "accept", "rollback", "cleanup",
        ):
            assert phase in component["supports"], (name, phase)


def test_every_policy_component_has_an_implemented_adapter():
    root = Path(__file__).resolve().parents[1]
    registry = AdapterRegistry(load_config(root / "config" / "fleet-policy.yml"))
    components = registry.describe_components()
    assert components
    assert all(component["implemented"] for component in components)


def test_finalizer_reports_scheduled_before_result_exists(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "fleet-policy.yml")

    class Runner:
        def run(self, argv, timeout=None):
            if "is-active" in argv and argv[-1].endswith(".timer"):
                return CommandResult(True, "active", "", 0, 1)
            if "is-active" in argv:
                return CommandResult(False, "inactive", "", 3, 1)
            return CommandResult(True, "Fri 2026-08-07 18:30:00 EDT", "", 0, 1)

    collector = FleetCollector(config, Runner())
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    value = collector.collect_finalizer()
    assert value["status"] == "scheduled"
    assert value["timer_active"] is True
