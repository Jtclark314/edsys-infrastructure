from __future__ import annotations

import json
import re
import shlex
import time
from typing import Any, Literal

from .config import FleetConfig
from .runner import CommandRunner


SAFE_NAME = re.compile(r"^[A-Za-z0-9_.:-]+$")
SAFE_UPID = re.compile(r"^UPID:[A-Za-z0-9_.:@-]+$")
EMBEDDED_UPID = re.compile(r"UPID:[A-Za-z0-9_.:@-]+")
GuestAction = Literal["start", "stop", "shutdown", "reboot", "reset", "suspend", "resume"]


class ProxmoxError(RuntimeError):
    pass


class ProxmoxClient:
    def __init__(self, config: FleetConfig, runner: CommandRunner | None = None):
        self.config = config
        self.runner = runner or CommandRunner(config.timeout)
        self.control_alias = str(config.proxmox.get("control_alias") or "pve-node0")

    def _call(self, method: str, path: str, *args: str, timeout: int | None = None) -> Any:
        if method not in {"get", "create", "set", "delete"}:
            raise ProxmoxError("Unsupported pvesh method")
        if not path.startswith("/") or any(ch in path for ch in "\n\r\t'"):
            raise ProxmoxError("Unsafe Proxmox API path")
        for value in args:
            if any(ch in value for ch in "\n\r\t"):
                raise ProxmoxError("Unsafe Proxmox API argument")
        tokens = ["pvesh", method, path, "--output-format", "json", *args]
        command = " ".join(shlex.quote(token) for token in tokens)
        result = self.runner.ssh(self.control_alias, command, timeout=timeout)
        if not result.ok:
            raise ProxmoxError(result.stderr or f"pvesh failed with {result.returncode}")
        try:
            return json.loads(result.stdout) if result.stdout else None
        except json.JSONDecodeError as exc:
            raise ProxmoxError("Proxmox returned invalid JSON") from exc

    def cluster_status(self) -> list[dict[str, Any]]:
        return self._call("get", "/cluster/status")

    def resources(self, resource_type: str | None = None) -> list[dict[str, Any]]:
        args = ("--type", resource_type) if resource_type else ()
        return self._call("get", "/cluster/resources", *args)

    def recent_tasks(self, limit: int = 25) -> list[dict[str, Any]]:
        return self._call("get", "/cluster/tasks")[: max(1, min(limit, 100))]

    def storages(self) -> list[dict[str, Any]]:
        return self._call("get", "/cluster/resources", "--type", "storage")

    def guest_status(self, node: str, vmid: int, guest_type: str = "qemu") -> dict[str, Any]:
        self._validate_target(node, vmid, guest_type)
        return self._call("get", f"/nodes/{node}/{guest_type}/{vmid}/status/current")

    def guest_config(self, node: str, vmid: int, guest_type: str = "qemu") -> dict[str, Any]:
        self._validate_target(node, vmid, guest_type)
        return self._call("get", f"/nodes/{node}/{guest_type}/{vmid}/config")

    def snapshots(self, node: str, vmid: int, guest_type: str = "qemu") -> list[dict[str, Any]]:
        self._validate_target(node, vmid, guest_type)
        return self._call("get", f"/nodes/{node}/{guest_type}/{vmid}/snapshot")

    def guest_action(self, node: str, vmid: int, action: GuestAction, guest_type: str = "qemu") -> Any:
        self._validate_target(node, vmid, guest_type)
        if action not in {"start", "stop", "shutdown", "reboot", "reset", "suspend", "resume"}:
            raise ProxmoxError("Unsupported guest action")
        return self._task_reference(
            self._call("create", f"/nodes/{node}/{guest_type}/{vmid}/status/{action}", timeout=90)
        )

    def create_snapshot(
        self,
        node: str,
        vmid: int,
        name: str,
        description: str = "EdSys Fleet Autopilot checkpoint",
        guest_type: str = "qemu",
        include_ram: bool = False,
    ) -> Any:
        self._validate_target(node, vmid, guest_type)
        if not SAFE_NAME.fullmatch(name):
            raise ProxmoxError("Snapshot name contains unsafe characters")
        description = re.sub(r"[^A-Za-z0-9 ._:-]", "", description)[:160]
        args = ["--snapname", name, "--description", description]
        if include_ram:
            args.extend(["--vmstate", "1"])
        return self._task_reference(
            self._call("create", f"/nodes/{node}/{guest_type}/{vmid}/snapshot", *args, timeout=300)
        )

    def rollback_snapshot(self, node: str, vmid: int, name: str, guest_type: str = "qemu") -> Any:
        self._validate_target(node, vmid, guest_type)
        if not SAFE_NAME.fullmatch(name):
            raise ProxmoxError("Snapshot name contains unsafe characters")
        return self._task_reference(
            self._call("create", f"/nodes/{node}/{guest_type}/{vmid}/snapshot/{name}/rollback", timeout=300)
        )

    def delete_snapshot(self, node: str, vmid: int, name: str, guest_type: str = "qemu") -> Any:
        self._validate_target(node, vmid, guest_type)
        if not SAFE_NAME.fullmatch(name):
            raise ProxmoxError("Snapshot name contains unsafe characters")
        return self._task_reference(
            self._call("delete", f"/nodes/{node}/{guest_type}/{vmid}/snapshot/{name}", timeout=300)
        )

    @staticmethod
    def _task_reference(value: Any) -> Any:
        """Normalize pvesh task output that embeds a warning before the UPID."""

        if isinstance(value, str):
            match = EMBEDDED_UPID.search(value)
            if match:
                return match.group(0)
        return value

    def task_status(self, node: str, upid: str) -> dict[str, Any]:
        if node not in set(map(str, self.config.proxmox.get("nodes") or [])):
            raise ProxmoxError(f"Unknown Proxmox node: {node}")
        if not SAFE_UPID.fullmatch(upid):
            raise ProxmoxError("Invalid Proxmox task identifier")
        return self._call("get", f"/nodes/{node}/tasks/{upid}/status")

    def wait_task(self, node: str, upid: str, *, timeout: int = 600) -> dict[str, Any]:
        deadline = time.monotonic() + max(1, timeout)
        while True:
            status = self.task_status(node, upid)
            if str(status.get("status") or "").lower() == "stopped":
                exitstatus = str(status.get("exitstatus") or "")
                if exitstatus != "OK":
                    raise ProxmoxError(f"Proxmox task failed: {exitstatus or 'unknown'}")
                return status
            if time.monotonic() >= deadline:
                raise ProxmoxError("Proxmox task timed out")
            time.sleep(1)

    def _validate_target(self, node: str, vmid: int, guest_type: str) -> None:
        nodes = set(map(str, self.config.proxmox.get("nodes") or []))
        if node not in nodes:
            raise ProxmoxError(f"Unknown Proxmox node: {node}")
        if guest_type not in {"qemu", "lxc"}:
            raise ProxmoxError("Guest type must be qemu or lxc")
        if vmid < 100 or vmid > 999999999:
            raise ProxmoxError("VMID is outside the accepted range")
