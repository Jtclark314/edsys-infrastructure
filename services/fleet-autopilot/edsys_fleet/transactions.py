from __future__ import annotations

import hashlib
from typing import Any

from .adapters import AdapterRegistry
from .config import FleetConfig
from .io import read_json
from .store import FleetStore, canonical_json, sanitize_evidence


class TransactionPlanError(RuntimeError):
    pass


class TransactionPlanner:
    def __init__(self, config: FleetConfig, store: FleetStore):
        self.config = config
        self.store = store
        self.registry = AdapterRegistry(config)

    def components(self) -> list[dict[str, Any]]:
        qualification = self._qualification_map()
        output = self.registry.describe_components()
        for item in output:
            keys = [
                (item["adapter"], host, item["id"])
                for host in item.get("hosts", [])
            ]
            item["qualified_hosts"] = [
                host
                for _, host, component in keys
                if qualification.get((item["adapter"], host, component), {}).get("status")
                == "qualified"
            ]
            item["fully_qualified"] = bool(keys) and len(item["qualified_hosts"]) == len(keys)
        return output

    def plan(
        self,
        *,
        host_id: str,
        component: str,
        action: str,
        requested_by: str,
        recovery_point_id: str | None = None,
    ) -> dict[str, Any]:
        if action not in {"upgrade", "rollback"}:
            raise TransactionPlanError("Transactions support only upgrade or rollback")
        policy = self.config.component(component)
        if host_id not in list(policy.get("hosts") or []):
            raise TransactionPlanError(f"{component} is not applicable to {host_id}")
        snapshot = read_json(self.config.state_root / "snapshot.json", {})
        host = next(
            (item for item in snapshot.get("hosts", []) if item.get("id") == host_id), None
        )
        if not host and not host_id.startswith("pve-"):
            raise TransactionPlanError(f"Host is absent from the current Fleet snapshot: {host_id}")
        current = self._current_version(host or {}, policy)
        candidate = self._desired(policy)
        if action == "rollback":
            if not recovery_point_id:
                raise TransactionPlanError("Rollback requires an explicit recovery point")
            recovery = next(
                (
                    item
                    for item in self.store.list_recovery_points(host_id, component)
                    if item["id"] == recovery_point_id
                ),
                None,
            )
            if not recovery or not recovery["verified"] or not recovery["compatible"]:
                raise TransactionPlanError("Recovery point is not verified and compatible")
            candidate = str(recovery["version"])
        preflight_basis = sanitize_evidence(
            {
                "host": {
                    "id": (host or {}).get("id"),
                    "status": (host or {}).get("status"),
                    "reachable": (host or {}).get("reachable"),
                    "versions": (host or {}).get("versions") or {},
                },
                "component_policy": policy,
                "recovery_point_id": recovery_point_id,
            }
        )
        preflight_hash = hashlib.sha256(canonical_json(preflight_basis).encode()).hexdigest()
        operations = [
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
        ]
        transaction = self.store.create_transaction(
            {
                "host_id": host_id,
                "component": component,
                "action": action,
                "risk_class": str(policy.get("risk_class") or "ordinary"),
                "current_version": current,
                "candidate": candidate,
                "policy_version": self.config.policy_version,
                "preflight_hash": preflight_hash,
                "recovery_point_id": recovery_point_id,
                "requested_by": requested_by,
                "operations": operations,
            }
        )
        self.store.transition_transaction(transaction["id"], "preflight")
        return self.store.transition_transaction(transaction["id"], "awaiting_approval")

    def _qualification_map(self) -> dict[tuple[str, str, str], dict[str, Any]]:
        with self.store.connect() as connection:
            rows = connection.execute("SELECT * FROM adapter_qualifications").fetchall()
        return {
            (str(row["adapter"]), str(row["host_id"]), str(row["component"])): dict(row)
            for row in rows
        }

    @staticmethod
    def _current_version(host: dict[str, Any], policy: dict[str, Any]) -> str:
        versions = dict(host.get("versions") or {})
        if policy.get("inventory_key"):
            return str(versions.get(str(policy["inventory_key"])) or "missing")
        keys = list(policy.get("inventory_keys") or [])
        if keys:
            return canonical_json({str(key): versions.get(str(key), "missing") for key in keys})
        return "discovered-at-preflight"

    @staticmethod
    def _desired(policy: dict[str, Any]) -> str:
        value = policy.get("desired", "unknown")
        return canonical_json(value) if isinstance(value, (dict, list)) else str(value)
