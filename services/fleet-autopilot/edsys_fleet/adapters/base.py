from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import FleetConfig
from ..runner import CommandRunner
from ..store import FleetStore


PHASES = (
    "discover",
    "resolve_candidate",
    "preflight",
    "checkpoint",
    "apply",
    "restart_or_reboot",
    "verify",
    "accept",
    "rollback",
    "cleanup",
)


@dataclass
class AdapterContext:
    config: FleetConfig
    store: FleetStore
    runner: CommandRunner
    host_id: str
    component: str
    transaction_id: str
    job_id: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class AdapterResult:
    status: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)
    recovery_point_id: str | None = None
    resumable: bool = True


class Adapter(Protocol):
    name: str
    idempotent_phases: set[str]

    def discover(self, context: AdapterContext) -> AdapterResult: ...
    def resolve_candidate(self, context: AdapterContext) -> AdapterResult: ...
    def preflight(self, context: AdapterContext) -> AdapterResult: ...
    def checkpoint(self, context: AdapterContext) -> AdapterResult: ...
    def apply(self, context: AdapterContext) -> AdapterResult: ...
    def restart_or_reboot(self, context: AdapterContext) -> AdapterResult: ...
    def verify(self, context: AdapterContext) -> AdapterResult: ...
    def accept(self, context: AdapterContext) -> AdapterResult: ...
    def rollback(self, context: AdapterContext) -> AdapterResult: ...
    def cleanup(self, context: AdapterContext) -> AdapterResult: ...


class UnqualifiedAdapter:
    """Fail-closed adapter used until a real rollback rehearsal is recorded."""

    def __init__(self, name: str):
        self.name = name
        self.idempotent_phases: set[str] = set()

    def _refuse(self, context: AdapterContext, phase: str) -> AdapterResult:
        return AdapterResult(
            status="manual_intervention_required",
            message=f"Adapter {self.name} is not qualified for {phase}; no mutation was attempted.",
            evidence={"adapter": self.name, "phase": phase, "component": context.component},
            resumable=False,
        )

    def discover(self, context: AdapterContext) -> AdapterResult:
        return self._refuse(context, "discover")

    def resolve_candidate(self, context: AdapterContext) -> AdapterResult:
        return self._refuse(context, "resolve_candidate")

    def preflight(self, context: AdapterContext) -> AdapterResult:
        return self._refuse(context, "preflight")

    def checkpoint(self, context: AdapterContext) -> AdapterResult:
        return self._refuse(context, "checkpoint")

    def apply(self, context: AdapterContext) -> AdapterResult:
        return self._refuse(context, "apply")

    def restart_or_reboot(self, context: AdapterContext) -> AdapterResult:
        return self._refuse(context, "restart_or_reboot")

    def verify(self, context: AdapterContext) -> AdapterResult:
        return self._refuse(context, "verify")

    def accept(self, context: AdapterContext) -> AdapterResult:
        return self._refuse(context, "accept")

    def rollback(self, context: AdapterContext) -> AdapterResult:
        return self._refuse(context, "rollback")

    def cleanup(self, context: AdapterContext) -> AdapterResult:
        return self._refuse(context, "cleanup")


class InventoryOnlyAdapter(UnqualifiedAdapter):
    name = "inventory-only"
    idempotent_phases = {"discover", "verify"}

    def __init__(self) -> None:
        super().__init__(self.name)
        self.idempotent_phases = {"discover", "verify"}

    def discover(self, context: AdapterContext) -> AdapterResult:
        return AdapterResult(
            status="passed",
            message="Inventory-only component discovered; execution remains governed externally.",
            evidence={"component": context.component, "host": context.host_id},
        )

    def verify(self, context: AdapterContext) -> AdapterResult:
        return self.discover(context)


class AdapterRegistry:
    def __init__(self, config: FleetConfig):
        self.config = config
        self._adapters: dict[str, Adapter] = {"inventory-only": InventoryOnlyAdapter()}
        self._register_policy_adapters()

    def _register_policy_adapters(self) -> None:
        from .real import GuardedManifestAdapter, NodeToolchainAdapter, ProxmoxGuestAdapter

        names = {str(value.get("adapter") or "") for value in self.config.components.values()}
        for name in sorted(names - {"", "inventory-only"}):
            if name == "node-toolchain":
                adapter: Adapter = NodeToolchainAdapter()
            elif name == "proxmox-guest":
                adapter = ProxmoxGuestAdapter()
            else:
                adapter = GuardedManifestAdapter(name)
            self._adapters[name] = adapter

    def register(self, adapter: Adapter) -> None:
        if adapter.name in self._adapters:
            raise ValueError(f"Adapter already registered: {adapter.name}")
        self._adapters[adapter.name] = adapter

    def get(self, name: str) -> Adapter:
        return self._adapters.get(name) or UnqualifiedAdapter(name)

    def describe_components(self) -> list[dict[str, Any]]:
        output = []
        for component, policy in self.config.components.items():
            name = str(policy.get("adapter") or "")
            declared_support = dict(policy.get("supports") or {})
            supports = {
                phase: bool(declared_support.get(phase, name != "inventory-only" or phase in {"discover", "verify"}))
                for phase in PHASES
            }
            output.append(
                {
                    "id": component,
                    "adapter": name,
                    "implemented": name in self._adapters,
                    "hosts": list(policy.get("hosts") or []),
                    "desired": policy.get("desired"),
                    "risk_class": policy.get("risk_class"),
                    "absence": policy.get("absence"),
                    "supports": supports,
                    "reboot_required": policy.get("reboot_required", False),
                    "observation_required": bool(policy.get("observation_required", False)),
                }
            )
        return output
