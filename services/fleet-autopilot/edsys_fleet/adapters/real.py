from __future__ import annotations

import hashlib
import json
import re
import shlex
from datetime import datetime, timezone
from typing import Any

from ..proxmox import ProxmoxClient
from ..runner import CommandResult
from .base import AdapterContext, AdapterResult, PHASES


SHA256 = re.compile(r"^[0-9a-f]{64}$")
SAFE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\\ -]{0,511}$")
READ_ONLY_PHASES = {"discover", "resolve_candidate", "preflight", "verify"}


class AdapterExecutionError(RuntimeError):
    pass


def _bounded_output(result: CommandResult) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "returncode": result.returncode,
        "elapsed_ms": result.elapsed_ms,
        "stdout": result.stdout[-6000:],
        "stderr": result.stderr[-3000:],
    }
    if result.stdout:
        try:
            parsed = json.loads(result.stdout)
            evidence["json"] = parsed
        except json.JSONDecodeError:
            pass
    return evidence


def _host_policy(context: AdapterContext) -> dict[str, Any]:
    value = next(
        (item for item in context.config.hosts if str(item.get("id")) == context.host_id),
        None,
    )
    if not value:
        raise AdapterExecutionError(f"Unknown host transport: {context.host_id}")
    return dict(value)


def _run_host(context: AdapterContext, argv: list[str], *, timeout: int = 1800) -> CommandResult:
    if not argv or any(not isinstance(item, str) or "\x00" in item for item in argv):
        raise AdapterExecutionError("Adapter command argv is invalid")
    host = _host_policy(context)
    transport = str(host.get("transport") or "")
    if transport == "local":
        return context.runner.run(argv, timeout=timeout)
    if transport == "ssh":
        alias = str(host.get("ssh_alias") or "")
        if not alias:
            raise AdapterExecutionError("SSH host is missing an alias")
        return context.runner.ssh(alias, shlex.join(argv), timeout=timeout)
    raise AdapterExecutionError(
        f"Transport {transport or 'missing'} requires the signed outbound agent and cannot execute locally"
    )


class GuardedManifestAdapter:
    """Executable adapter for components whose exact operations live in a frozen plan manifest.

    This is not a placeholder: every phase must declare either an exact argv and
    acceptance predicate or an explicit, justified no-op. Apply and rollback can
    never be no-ops. The manifest is part of the transaction plan hash.
    """

    idempotent_phases = READ_ONLY_PHASES | {"cleanup"}

    def __init__(self, name: str):
        self.name = name

    def _manifest(self, context: AdapterContext) -> dict[str, Any]:
        manifest = context.parameters.get("adapter_manifest")
        if not isinstance(manifest, dict):
            raise AdapterExecutionError(
                f"{self.name} requires an immutable adapter_manifest frozen into the approved plan"
            )
        if str(manifest.get("adapter")) != self.name:
            raise AdapterExecutionError("Adapter manifest name does not match policy")
        candidate = manifest.get("candidate")
        rollback = manifest.get("rollback")
        if not isinstance(candidate, dict) or not SHA256.fullmatch(str(candidate.get("sha256") or "")):
            raise AdapterExecutionError("Candidate manifest requires an immutable SHA-256")
        if not str(candidate.get("source") or "").startswith(("https://", "git+", "private://")):
            raise AdapterExecutionError("Candidate source is not an accepted official/private source")
        if not isinstance(rollback, dict) or not SHA256.fullmatch(str(rollback.get("sha256") or "")):
            raise AdapterExecutionError("Rollback manifest requires a retained immutable SHA-256")
        phases = manifest.get("phases")
        if not isinstance(phases, dict) or any(phase not in phases for phase in PHASES):
            raise AdapterExecutionError("Adapter manifest must define all ten lifecycle phases")
        if "verify_rollback" not in phases:
            raise AdapterExecutionError(
                "Adapter manifest must define verify_rollback for observable recovery acceptance"
            )
        if context.parameters.get("_fleet_transaction_action") == "rollback":
            if "rollback_selected" not in phases or "restore_checkpoint" not in phases:
                raise AdapterExecutionError(
                    "Rollback plans require rollback_selected and restore_checkpoint phases"
                )
        return manifest

    def _phase(self, context: AdapterContext, phase: str) -> AdapterResult:
        manifest = self._manifest(context)
        specification_name = phase
        if (
            phase == "verify"
            and context.parameters.get("_fleet_verification_target") == "rollback"
        ):
            specification_name = "verify_rollback"
        if phase == "rollback":
            target = context.parameters.get("_fleet_rollback_target")
            if target == "selected":
                specification_name = "rollback_selected"
            elif target == "safety":
                specification_name = "restore_checkpoint"
        specification = manifest["phases"].get(specification_name)
        if not isinstance(specification, dict):
            raise AdapterExecutionError(f"Manifest phase {specification_name} is invalid")
        if specification.get("noop"):
            if phase in {"apply", "rollback"}:
                raise AdapterExecutionError(f"Mutating phase {phase} cannot be a no-op")
            reason = str(specification.get("reason") or "")
            if len(reason) < 12:
                raise AdapterExecutionError(f"No-op phase {phase} requires a concrete reason")
            return AdapterResult(
                status="passed",
                message=f"{self.name} {phase} is intentionally non-mutating: {reason}",
                evidence={"phase": phase, "reason": reason},
            )
        argv = specification.get("argv")
        if not isinstance(argv, list) or not argv or any(not isinstance(item, str) for item in argv):
            raise AdapterExecutionError(f"Manifest phase {phase} requires argv")
        result = _run_host(context, list(argv), timeout=int(specification.get("timeout_seconds") or 1800))
        evidence = _bounded_output(result)
        if not result.ok:
            raise AdapterExecutionError(
                f"{self.name} {phase} failed with exit code {result.returncode}: {result.stderr[-500:]}"
            )
        required_text = specification.get("stdout_contains")
        if required_text and str(required_text) not in result.stdout:
            raise AdapterExecutionError(f"{self.name} {phase} acceptance text was absent")
        expected_sha = specification.get("artifact_sha256")
        artifact_ref = specification.get("artifact_ref")
        recovery_point_id = None
        if phase == "checkpoint":
            if not artifact_ref or not SHA256.fullmatch(str(expected_sha or "")):
                raise AdapterExecutionError("Checkpoint phase requires artifact_ref and artifact_sha256")
            checkpoint_version = str(
                context.parameters.get("_fleet_checkpoint_version")
                or manifest["rollback"].get("version")
                or "unknown"
            )
            recovery = context.store.add_recovery_point(
                {
                    "host_id": context.host_id,
                    "component": context.component,
                    # During an ordinary upgrade the checkpoint is the prior/rollback
                    # version. During an explicit rollback it is instead a fresh
                    # safety image of the current version. The runner derives this
                    # value from the immutable transaction rather than trusting a
                    # caller-supplied recovery label.
                    "version": checkpoint_version,
                    "checksum": str(expected_sha),
                    "checkpoint_type": str(specification.get("checkpoint_type") or "manifest"),
                    "artifact_ref": str(artifact_ref),
                    "compatible": True,
                    "verified": True,
                    "accepted": False,
                    "metadata": {"transaction_id": context.transaction_id, "adapter": self.name},
                }
            )
            recovery_point_id = recovery["id"]
        return AdapterResult(
            status="passed",
            message=f"{self.name} {phase} completed from the immutable plan manifest.",
            evidence=evidence,
            recovery_point_id=recovery_point_id,
            resumable=phase in self.idempotent_phases,
        )

    def discover(self, context: AdapterContext) -> AdapterResult:
        return self._phase(context, "discover")

    def resolve_candidate(self, context: AdapterContext) -> AdapterResult:
        return self._phase(context, "resolve_candidate")

    def preflight(self, context: AdapterContext) -> AdapterResult:
        return self._phase(context, "preflight")

    def checkpoint(self, context: AdapterContext) -> AdapterResult:
        return self._phase(context, "checkpoint")

    def apply(self, context: AdapterContext) -> AdapterResult:
        return self._phase(context, "apply")

    def restart_or_reboot(self, context: AdapterContext) -> AdapterResult:
        return self._phase(context, "restart_or_reboot")

    def verify(self, context: AdapterContext) -> AdapterResult:
        return self._phase(context, "verify")

    def accept(self, context: AdapterContext) -> AdapterResult:
        return self._phase(context, "accept")

    def rollback(self, context: AdapterContext) -> AdapterResult:
        return self._phase(context, "rollback")

    def cleanup(self, context: AdapterContext) -> AdapterResult:
        return self._phase(context, "cleanup")


class NodeToolchainAdapter:
    name = "node-toolchain"
    idempotent_phases = READ_ONLY_PHASES | {"checkpoint", "restart_or_reboot", "cleanup"}
    default_script = (
        r"C:\Users\jtcla\AppData\Local\EdSys-Private\fleet-agent\adapters\node-toolchain-adapter.ps1"
    )

    def _invoke(self, context: AdapterContext, action: str) -> AdapterResult:
        if context.host_id != "nimo":
            phase = {
                "Discover": "discover",
                "ResolveCandidate": "resolve_candidate",
                "Preflight": "preflight",
                "Checkpoint": "checkpoint",
                "Apply": "apply",
                "RestartOrReboot": "restart_or_reboot",
                "Verify": "verify",
                "Accept": "accept",
                "Rollback": "rollback",
                "Cleanup": "cleanup",
            }[action]
            return GuardedManifestAdapter(self.name)._phase(context, phase)
        script = str(context.parameters.get("adapter_script") or self.default_script)
        if not SAFE_VALUE.fullmatch(script):
            raise AdapterExecutionError("Node adapter script path is invalid")
        candidate = str(context.parameters.get("candidate_version") or "24.19.0")
        rollback = str(context.parameters.get("rollback_version") or "24.15.0")
        npm = str(context.parameters.get("expected_npm_version") or "12.0.2")
        for value in (candidate, rollback, npm):
            if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value):
                raise AdapterExecutionError("Node/npm version is invalid")
        host = _host_policy(context)
        alias = str(host.get("ssh_alias") or "")
        command = (
            f'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{script}" '
            f'-Action {action} -RunId "{context.transaction_id}" '
            f'-CandidateVersion "{candidate}" -RollbackVersion "{rollback}" '
            f'-ExpectedNpmVersion "{npm}"'
        )
        if context.parameters.get("qualification"):
            command += " -QualificationRehearsal"
        result = context.runner.ssh(alias, command, timeout=1800)
        evidence = _bounded_output(result)
        if not result.ok:
            raise AdapterExecutionError(
                f"Nimo Node {action} failed with exit code {result.returncode}: {result.stderr[-500:]}"
            )
        payload = evidence.get("json")
        if not isinstance(payload, dict) or payload.get("status") != "passed":
            raise AdapterExecutionError(f"Nimo Node {action} returned no accepted JSON result")
        recovery_id = None
        if action == "Checkpoint":
            checksum = str(payload.get("checkpointSha256") or "")
            if not SHA256.fullmatch(checksum):
                raise AdapterExecutionError("Nimo Node checkpoint did not return an immutable SHA-256")
            recovery = context.store.add_recovery_point(
                {
                    "host_id": context.host_id,
                    "component": context.component,
                    "version": str(payload.get("version") or rollback),
                    "checksum": checksum,
                    "checkpoint_type": "windows-node-toolchain",
                    "artifact_ref": str(payload.get("checkpointPath") or "private-runtime"),
                    "compatible": True,
                    "verified": True,
                    "accepted": False,
                    "metadata": {"transaction_id": context.transaction_id, "package_id": "OpenJS.NodeJS.LTS"},
                }
            )
            recovery_id = recovery["id"]
        return AdapterResult(
            status="passed",
            message=f"Nimo atomic Node/npm phase {action} passed.",
            evidence=payload,
            recovery_point_id=recovery_id,
            resumable=action.lower() in {item.lower() for item in self.idempotent_phases},
        )

    def discover(self, context: AdapterContext) -> AdapterResult:
        return self._invoke(context, "Discover")

    def resolve_candidate(self, context: AdapterContext) -> AdapterResult:
        return self._invoke(context, "ResolveCandidate")

    def preflight(self, context: AdapterContext) -> AdapterResult:
        return self._invoke(context, "Preflight")

    def checkpoint(self, context: AdapterContext) -> AdapterResult:
        return self._invoke(context, "Checkpoint")

    def apply(self, context: AdapterContext) -> AdapterResult:
        return self._invoke(context, "Apply")

    def restart_or_reboot(self, context: AdapterContext) -> AdapterResult:
        return self._invoke(context, "RestartOrReboot")

    def verify(self, context: AdapterContext) -> AdapterResult:
        return self._invoke(context, "Verify")

    def accept(self, context: AdapterContext) -> AdapterResult:
        return self._invoke(context, "Accept")

    def rollback(self, context: AdapterContext) -> AdapterResult:
        if (
            context.host_id == "nimo"
            and context.parameters.get("_fleet_rollback_target") == "safety"
        ):
            return self._invoke(context, "Apply")
        return self._invoke(context, "Rollback")

    def cleanup(self, context: AdapterContext) -> AdapterResult:
        return self._invoke(context, "Cleanup")


class ProxmoxGuestAdapter:
    name = "proxmox-guest"
    idempotent_phases = READ_ONLY_PHASES | {"restart_or_reboot", "cleanup"}

    def _target(self, context: AdapterContext) -> tuple[ProxmoxClient, str, int, str, str]:
        parameters = context.parameters
        node = str(parameters.get("node") or context.host_id)
        vmid = int(parameters.get("vmid") or 0)
        guest_type = str(parameters.get("guest_type") or "lxc")
        operation = str(parameters.get("operation") or "")
        if operation not in {
            "start", "stop", "shutdown", "reboot", "reset", "suspend", "resume",
            "create_snapshot", "rollback_snapshot", "delete_snapshot", "canary_lifecycle",
        }:
            raise AdapterExecutionError("Proxmox guest operation is not allowlisted")
        return ProxmoxClient(context.config, context.runner), node, vmid, guest_type, operation

    def discover(self, context: AdapterContext) -> AdapterResult:
        client, node, vmid, guest_type, _ = self._target(context)
        status = client.guest_status(node, vmid, guest_type)
        config = client.guest_config(node, vmid, guest_type)
        return AdapterResult("passed", "Proxmox guest discovered through pvesh.", {"status": status, "config_digest": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()})

    def resolve_candidate(self, context: AdapterContext) -> AdapterResult:
        _, node, vmid, guest_type, operation = self._target(context)
        return AdapterResult("passed", "Requested Proxmox lifecycle operation resolved.", {"node": node, "vmid": vmid, "guest_type": guest_type, "operation": operation})

    def preflight(self, context: AdapterContext) -> AdapterResult:
        client, node, vmid, guest_type, _ = self._target(context)
        cluster = client.cluster_status()
        nodes = [item for item in cluster if item.get("type") == "node"]
        quorate = any(item.get("type") == "cluster" and int(item.get("quorate") or 0) == 1 for item in cluster)
        if not quorate or sum(int(item.get("online") or 0) for item in nodes) < 4:
            raise AdapterExecutionError("Proxmox preflight requires four online nodes and quorum")
        failed = [item for item in client.recent_tasks(50) if str(item.get("status") or "").lower() not in {"", "ok"}]
        if failed:
            raise AdapterExecutionError("Proxmox has failed recent tasks; mutation is blocked")
        client.guest_status(node, vmid, guest_type)
        return AdapterResult("passed", "Proxmox quorum, node, task, and guest preflight passed.", {"quorate": quorate, "nodes_online": 4, "failed_tasks": 0})

    def checkpoint(self, context: AdapterContext) -> AdapterResult:
        client, node, vmid, guest_type, operation = self._target(context)
        name = str(context.parameters.get("checkpoint_name") or f"fleet-{context.transaction_id[-18:]}")
        if operation == "create_snapshot":
            name = str(context.parameters.get("name") or name)
        upid = client.create_snapshot(node, vmid, name, "Fleet Autopilot transaction checkpoint", guest_type)
        client.wait_task(node, str(upid), timeout=600)
        snapshots = client.snapshots(node, vmid, guest_type)
        snapshot = next((item for item in snapshots if item.get("name") == name), None)
        if not snapshot:
            raise AdapterExecutionError("Proxmox checkpoint snapshot was not observable after completion")
        checksum = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
        recovery = context.store.add_recovery_point(
            {
                "host_id": context.host_id,
                "component": context.component,
                "version": f"{guest_type}-{vmid}:{name}",
                "checksum": checksum,
                "checkpoint_type": "proxmox-snapshot",
                "artifact_ref": f"pve://{node}/{guest_type}/{vmid}/snapshot/{name}",
                "compatible": True,
                "verified": True,
                "accepted": False,
                "metadata": {"node": node, "vmid": vmid, "guest_type": guest_type, "snapshot": name},
            }
        )
        return AdapterResult("passed", "Proxmox recovery snapshot completed and verified.", {"upid": upid, "snapshot": name, "sha256": checksum}, recovery["id"], False)

    def apply(self, context: AdapterContext) -> AdapterResult:
        client, node, vmid, guest_type, operation = self._target(context)
        if operation == "canary_lifecycle":
            before = client.guest_status(node, vmid, guest_type)
            first = "stop" if before.get("status") == "running" else "start"
            upid = client.guest_action(node, vmid, first, guest_type)
            client.wait_task(node, str(upid), timeout=300)
            second = "start" if first == "stop" else "stop"
            upid2 = client.guest_action(node, vmid, second, guest_type)
            client.wait_task(node, str(upid2), timeout=300)
            return AdapterResult("passed", "Disposable Proxmox canary lifecycle completed.", {"operations": [first, second], "upids": [upid, upid2]}, resumable=False)
        if operation in {"create_snapshot", "rollback_snapshot", "delete_snapshot"}:
            # The checkpoint or explicit rollback/cleanup phase owns snapshot mutations.
            return AdapterResult("passed", "Snapshot mutation is assigned to its dedicated lifecycle phase.", {"operation": operation})
        upid = client.guest_action(node, vmid, operation, guest_type)  # type: ignore[arg-type]
        client.wait_task(node, str(upid), timeout=300)
        return AdapterResult("passed", f"Proxmox guest operation {operation} completed.", {"upid": upid}, resumable=False)

    def restart_or_reboot(self, context: AdapterContext) -> AdapterResult:
        return AdapterResult("passed", "No additional restart is required beyond the selected Proxmox operation.", {"reboot_required": False})

    def verify(self, context: AdapterContext) -> AdapterResult:
        client, node, vmid, guest_type, operation = self._target(context)
        status = client.guest_status(node, vmid, guest_type)
        expected = context.parameters.get("expected_status")
        if expected and status.get("status") != expected:
            raise AdapterExecutionError(f"Proxmox guest status mismatch: expected {expected}, found {status.get('status')}")
        if operation == "canary_lifecycle" and status.get("status") not in {"running", "stopped"}:
            raise AdapterExecutionError("Proxmox canary finished in an invalid lifecycle state")
        return AdapterResult("passed", "Proxmox guest status verified after mutation.", {"status": status})

    def accept(self, context: AdapterContext) -> AdapterResult:
        return AdapterResult("passed", "Proxmox guest operation accepted from observable API evidence.", {"accepted_at": datetime.now(timezone.utc).isoformat()})

    def rollback(self, context: AdapterContext) -> AdapterResult:
        client, node, vmid, guest_type, _ = self._target(context)
        recovery_id = str(context.parameters.get("recovery_point_id") or "")
        recovery = next((item for item in context.store.list_recovery_points(context.host_id, context.component) if item["id"] == recovery_id), None)
        if not recovery:
            # Qualification rehearsal uses the checkpoint created in this transaction.
            recovery = next((item for item in context.store.list_recovery_points(context.host_id, context.component) if item.get("metadata", {}).get("transaction_id") == context.transaction_id), None)
        if not recovery:
            raise AdapterExecutionError("Proxmox rollback requires the exact verified recovery point")
        name = str(recovery.get("metadata", {}).get("snapshot") or "")
        upid = client.rollback_snapshot(node, vmid, name, guest_type)
        client.wait_task(node, str(upid), timeout=600)
        return AdapterResult("passed", "Proxmox guest rolled back to the verified checkpoint.", {"upid": upid, "snapshot": name}, resumable=False)

    def cleanup(self, context: AdapterContext) -> AdapterResult:
        # Recovery snapshots are retained by policy; deletion is a separate approved plan.
        return AdapterResult("passed", "Verified Proxmox recovery snapshot retained by the two-known-good floor.", {"retained": True})
