from __future__ import annotations

from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from .config import load_config
from .proxmox import ProxmoxClient


mcp = FastMCP(
    "EdSys Proxmox",
    instructions=(
        "Full-power private Proxmox operations for the EdSys cluster. Read current state before mutation, "
        "prefer a snapshot before disruptive guest changes, and return the Proxmox task identifier."
    ),
    json_response=True,
)


def client() -> ProxmoxClient:
    return ProxmoxClient(load_config())


@mcp.tool()
def proxmox_cluster_status() -> dict[str, Any]:
    """Return cluster quorum and all configured Proxmox node states."""
    api = client()
    return {"cluster": api.cluster_status(), "storages": api.storages(), "recent_tasks": api.recent_tasks(20)}


@mcp.tool()
def proxmox_list_guests() -> list[dict[str, Any]]:
    """List every QEMU/LXC guest with node, state, utilization, and uptime."""
    return client().resources("vm")


@mcp.tool()
def proxmox_guest_details(node: str, vmid: int, guest_type: Literal["qemu", "lxc"] = "qemu") -> dict[str, Any]:
    """Return live status, configuration, and snapshot inventory for one guest."""
    api = client()
    return {
        "status": api.guest_status(node, vmid, guest_type),
        "config": api.guest_config(node, vmid, guest_type),
        "snapshots": api.snapshots(node, vmid, guest_type),
    }


@mcp.tool()
def proxmox_guest_action(
    node: str,
    vmid: int,
    action: Literal["start", "stop", "shutdown", "reboot", "reset", "suspend", "resume"],
    guest_type: Literal["qemu", "lxc"] = "qemu",
) -> dict[str, Any]:
    """Execute a power/lifecycle action and return the Proxmox task identifier."""
    value = client().guest_action(node, vmid, action, guest_type)
    return {"submitted": True, "node": node, "vmid": vmid, "action": action, "upid": value}


@mcp.tool()
def proxmox_create_snapshot(
    node: str,
    vmid: int,
    name: str,
    description: str = "EdSys Fleet Autopilot checkpoint",
    guest_type: Literal["qemu", "lxc"] = "qemu",
    include_ram: bool = False,
) -> dict[str, Any]:
    """Create a named guest snapshot and return the Proxmox task identifier."""
    value = client().create_snapshot(node, vmid, name, description, guest_type, include_ram)
    return {"submitted": True, "node": node, "vmid": vmid, "snapshot": name, "upid": value}


@mcp.tool()
def proxmox_rollback_snapshot(
    node: str,
    vmid: int,
    name: str,
    guest_type: Literal["qemu", "lxc"] = "qemu",
) -> dict[str, Any]:
    """Roll a guest back to an existing named snapshot."""
    value = client().rollback_snapshot(node, vmid, name, guest_type)
    return {"submitted": True, "node": node, "vmid": vmid, "snapshot": name, "upid": value}


@mcp.tool()
def proxmox_delete_snapshot(
    node: str,
    vmid: int,
    name: str,
    guest_type: Literal["qemu", "lxc"] = "qemu",
) -> dict[str, Any]:
    """Delete an existing named guest snapshot."""
    value = client().delete_snapshot(node, vmid, name, guest_type)
    return {"submitted": True, "node": node, "vmid": vmid, "snapshot": name, "upid": value}


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
