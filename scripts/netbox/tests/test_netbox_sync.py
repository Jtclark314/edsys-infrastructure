from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "edsys-netbox-sync"
LOADER = importlib.machinery.SourceFileLoader("edsys_netbox_sync", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC
MODULE = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(MODULE)


def test_networkless_benchmark_canary_does_not_invent_net0():
    plan = MODULE.Plan("sync-proxmox")
    MODULE.add_virtualization(
        plan,
        {"proxmox_resources": []},
        [
            {
                "vmid": 390,
                "name": "fleet-canary",
                "node": "pve-edcore",
                "type": "lxc",
                "status": "stopped",
                "cores": 1,
                "memory_mb": 512,
                "onboot": False,
                "bridge": None,
                "mac": None,
                "source": "test",
                "confidence": "high",
                "last_verified": "2026-08-06",
            }
        ],
    )
    canary = [item for item in plan.operations if "390" in item.get("key", "")]
    assert [item["key"] for item in canary] == ["vm:390"]
    assert canary[0]["payload"]["name"] == "fleet-canary"
    assert {item["$ref"] for item in canary[0]["payload"]["tags"]} >= {
        "tag:benchmark-canary"
    }


def test_validator_allows_only_tagged_networkless_benchmark_canary():
    class API:
        def list(self, path):
            if path == "virtualization/virtual-machines/":
                return [
                    {
                        "name": "fleet-canary",
                        "cluster": {"name": "gadgetlab"},
                        "primary_ip4": None,
                        "tags": [{"slug": "benchmark-canary"}],
                    }
                ]
            if path in {"dcim/devices/", "ipam/ip-addresses/", "ipam/prefixes/", "ipam/services/"}:
                return [{"name": "required", "parent": {"id": 1}, "address": "192.0.2.1/32"}]
            return []

    result = MODULE.validate_inventory(API())
    assert not any("fleet-canary" in error for error in result["errors"])
