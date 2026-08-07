from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .collector import FleetCollector
from .config import FleetConfig
from .io import read_json, utc_now, write_json_atomic
from .proxmox import ProxmoxClient, ProxmoxError
from .runner import CommandRunner


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
SUPPORTED_ACTIONS = {"inspect", "verify", "upgrade", "rollback", "proxmox"}


class FleetJobError(RuntimeError):
    pass


class FleetJobRunner:
    def __init__(self, config: FleetConfig, runner: CommandRunner | None = None):
        self.config = config
        self.runner = runner or CommandRunner(config.timeout)
        self.collector = FleetCollector(config, self.runner)
        self.proxmox = ProxmoxClient(config, self.runner)

    def process_one(self) -> dict[str, Any] | None:
        pending = self.config.state_root / "queue" / "pending"
        running = self.config.state_root / "queue" / "running"
        pending.mkdir(parents=True, exist_ok=True)
        running.mkdir(parents=True, exist_ok=True)
        items = sorted(pending.glob("*.json"), key=lambda path: path.stat().st_mtime)
        if not items:
            return None
        source = items[0]
        destination = running / source.name
        try:
            os.replace(source, destination)
        except FileNotFoundError:
            return None
        job = read_json(destination, {})
        if not isinstance(job, dict):
            destination.unlink(missing_ok=True)
            return None
        job.update({"status": "running", "started_at": utc_now()})
        write_json_atomic(destination, job, mode=0o660)
        try:
            result = self.execute(job)
            job.update({"status": "complete", "completed_at": utc_now(), "result": result, "error": None})
        except Exception as exc:  # worker must persist a bounded failure result
            job.update({"status": "failed", "completed_at": utc_now(), "result": {}, "error": f"{type(exc).__name__}: {exc}"})
        completed = self.config.state_root / "queue" / "completed" / destination.name
        write_json_atomic(completed, job, mode=0o660)
        destination.unlink(missing_ok=True)
        self._trim_history()
        return job

    def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        action = str(job.get("action") or "")
        if action not in SUPPORTED_ACTIONS:
            raise FleetJobError(f"Unsupported action: {action}")
        if action == "inspect":
            snapshot = self.collector.collect()
            return {"message": "Fleet inspection completed.", "summary": snapshot["summary"], "generated_at": snapshot["generated_at"]}
        if action == "verify":
            snapshot = self.collector.collect()
            target = str(job.get("target") or "all")
            selected = [host for host in snapshot["hosts"] if target in {"all", host["id"]}]
            passed = all(host["status"] != "offline" and not any(d.get("severity") == "critical" for d in host.get("drift", [])) for host in selected)
            return {"message": "Acceptance verification completed.", "target": target, "passed": passed, "hosts": [{"id": host["id"], "status": host["status"], "drift": len(host.get("drift", []))} for host in selected]}
        if action == "upgrade":
            return self._upgrade(job)
        if action == "rollback":
            return self._rollback(job)
        return self._proxmox_action(job)

    def _upgrade(self, job: dict[str, Any]) -> dict[str, Any]:
        target = str(job.get("target") or "")
        component = str(job.get("component") or "")
        params = dict(job.get("parameters") or {})
        if not SAFE_ID.fullmatch(target) or not SAFE_ID.fullmatch(component):
            raise FleetJobError("Upgrade target/component is invalid")
        snapshot = read_json(self.config.state_root / "snapshot.json", {})
        host = next((item for item in snapshot.get("hosts", []) if item.get("id") == target), None)
        if host is None:
            raise FleetJobError(f"Unknown fleet target: {target}")
        current = str((host.get("versions") or {}).get(component) or "unknown")
        approved = str(self.config.approved.get(component) or params.get("candidate") or "unknown")
        if current == approved:
            return {"message": f"{target} {component} already matches the approved release.", "status": "no_change", "current": current, "approved": approved}
        if component == "codex":
            return self._codex_transaction(target, params, rollback=False)
        plan = {
            "target": target,
            "component": component,
            "current": current,
            "approved": approved,
            "checkpoint_required": True,
            "rollback_required": True,
            "acceptance": ["version parity", "health check", "network", "browser path", "host capability smoke"],
        }
        return {"message": "Guarded upgrade plan generated; component adapter requires a staged candidate before promotion.", "status": "planned", "plan": plan}

    def _rollback(self, job: dict[str, Any]) -> dict[str, Any]:
        target = str(job.get("target") or "")
        component = str(job.get("component") or "")
        params = dict(job.get("parameters") or {})
        if component == "codex":
            return self._codex_transaction(target, params, rollback=True)
        if component == "proxmox-snapshot":
            return self._proxmox_action({**job, "parameters": {**params, "operation": "rollback_snapshot"}})
        raise FleetJobError("Rollback requires a supported component and explicit transaction/snapshot identifier")

    def _codex_transaction(self, target: str, params: dict[str, Any], *, rollback: bool) -> dict[str, Any]:
        run_id = str(params.get("run_id") or "")
        if not SAFE_ID.fullmatch(run_id):
            raise FleetJobError("A valid staged Codex transaction run_id is required")
        if target == "9950x":
            helper = Path.home() / "code" / "EdSys-Master" / "tools" / "codex-hub" / "codex-version-upgrade.py"
            command = ["python3", str(helper), "rollback" if rollback else str(params.get("phase") or "status"), "--run", run_id]
            if rollback:
                command.append("--from-watchdog") if params.get("from_watchdog") else None
            result = self.runner.run(command, timeout=600)
        elif target == "nimo":
            helper = r"C:\EdSys\Tools\nimo-codex-version-upgrade.ps1"
            phase = "Rollback" if rollback else str(params.get("phase") or "Status")
            command = f'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{helper}" -Action {phase} -RunId "{run_id}"'
            result = self.runner.ssh("nimo-laptop", command, timeout=600)
        else:
            raise FleetJobError("Codex transaction adapter currently supports 9950x and Nimo")
        if not result.ok:
            raise FleetJobError(result.stderr or "Codex transaction failed")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = {"output": result.stdout[-4000:]}
        return {"message": "Codex rollback completed." if rollback else "Codex transaction phase completed.", "status": "executed", "transaction": payload}

    def _proxmox_action(self, job: dict[str, Any]) -> dict[str, Any]:
        params = dict(job.get("parameters") or {})
        operation = str(params.get("operation") or "")
        node = str(params.get("node") or "")
        vmid = int(params.get("vmid") or 0)
        guest_type = str(params.get("guest_type") or "qemu")
        if operation in {"start", "stop", "shutdown", "reboot", "reset", "suspend", "resume"}:
            value = self.proxmox.guest_action(node, vmid, operation, guest_type)  # type: ignore[arg-type]
        elif operation == "create_snapshot":
            value = self.proxmox.create_snapshot(node, vmid, str(params.get("name") or ""), str(params.get("description") or "EdSys Fleet Autopilot checkpoint"), guest_type, bool(params.get("include_ram")))
        elif operation == "rollback_snapshot":
            value = self.proxmox.rollback_snapshot(node, vmid, str(params.get("name") or ""), guest_type)
        elif operation == "delete_snapshot":
            value = self.proxmox.delete_snapshot(node, vmid, str(params.get("name") or ""), guest_type)
        else:
            raise FleetJobError("Unsupported Proxmox operation")
        self.collector.collect()
        return {"message": f"Proxmox operation {operation} submitted.", "status": "executed", "upid": value, "node": node, "vmid": vmid}

    def _trim_history(self, keep: int = 200) -> None:
        root = self.config.state_root / "queue" / "completed"
        items = sorted(root.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for item in items[keep:]:
            item.unlink(missing_ok=True)


def queue_job(config: FleetConfig, action: str, target: str = "all", component: str = "", parameters: dict[str, Any] | None = None, requested_by: str = "portal") -> dict[str, Any]:
    if action not in SUPPORTED_ACTIONS:
        raise FleetJobError(f"Unsupported action: {action}")
    job_id = f"fleet-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    job = {
        "id": job_id,
        "action": action,
        "target": target,
        "component": component,
        "parameters": parameters or {},
        "requested_by": requested_by,
        "requested_at": utc_now(),
        "status": "pending",
    }
    write_json_atomic(config.state_root / "queue" / "pending" / f"{job_id}.json", job, mode=0o660)
    return job


def list_jobs(config: FleetConfig, limit: int = 40) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for status in ("running", "pending", "completed"):
        root = config.state_root / "queue" / status
        for path in root.glob("*.json") if root.exists() else []:
            value = read_json(path, {})
            if isinstance(value, dict):
                jobs.append(value)
    return sorted(jobs, key=lambda job: str(job.get("requested_at") or ""), reverse=True)[:limit]
