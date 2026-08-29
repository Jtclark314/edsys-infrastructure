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
                "node": "pve-node0",
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


def test_retired_device_is_offline_and_its_address_is_deprecated():
    plan = MODULE.Plan("sync-network")
    MODULE.add_network_inventory(
        plan,
        {
            "devices": [
                {
                    "hostname": "pve-edcore",
                    "category": "virtualization",
                    "role": "Retired Proxmox identity",
                    "status": "retired-destroyed-awaiting-omarchy",
                    "ip": "192.168.50.54",
                    "interfaces": [
                        {
                            "name": "vmbr0",
                            "state": "DOWN",
                            "ip": "192.168.50.54/24",
                        }
                    ],
                    "source": "test",
                    "confidence": "high",
                    "last_verified": "2026-08-29",
                }
            ],
            "subnets": [],
            "proxmox_resources": [],
            "proxmox_cluster": {"nodes": []},
        },
    )
    operations = {item["key"]: item for item in plan.operations}
    device = operations["device:pve-edcore"]["payload"]
    interface = operations["interface:pve-edcore:vmbr0"]["payload"]
    address = operations["ip:192.168.50.54/24"]["payload"]
    assert device["status"] == "offline"
    assert device["custom_fields"]["retired"] is True
    assert {item["$ref"] for item in device["tags"]} >= {"tag:retired"}
    assert interface["enabled"] is False
    assert address["status"] == "deprecated"
    assert address["dns_name"] == ""


def test_retired_vm_is_offline_and_excluded_from_sanitized_export():
    plan = MODULE.Plan("sync-proxmox")
    MODULE.add_virtualization(
        plan,
        {
            "proxmox_resources": [
                {
                    "vmid": 321,
                    "name": "edcore-ops",
                    "node": "pve-edcore",
                    "type": "qemu",
                    "status": "destroyed-retired",
                    "guest_ip": "192.168.50.79",
                    "bridge": "vmbr0",
                    "source": "test",
                    "confidence": "high",
                    "last_verified": "2026-08-29",
                }
            ]
        },
        [],
    )
    operations = {item["key"]: item for item in plan.operations}
    vm = operations["vm:321"]["payload"]
    interface = operations["vm-interface:321:net0"]["payload"]
    address = operations["ip:192.168.50.79/24"]["payload"]
    assert vm["status"] == "offline"
    assert vm["custom_fields"]["retired"] is True
    assert interface["enabled"] is False
    assert address["status"] == "deprecated"
    assert address["dns_name"] == ""

    class API:
        def list(self, path):
            if path == "virtualization/virtual-machines/":
                return [{"name": "edcore-ops", "custom_fields": {"retired": True}}]
            return []

    assert MODULE.sanitized_export(API())["virtual_machines"] == []
