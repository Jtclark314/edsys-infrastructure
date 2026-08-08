from __future__ import annotations

from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from .config import FleetConfig, load_config
from .io import read_json
from .jobs import queue_job
from .store import FleetStore
from .transactions import TransactionPlanner

mcp = FastMCP(
    "EdSys Fleet Control",
    instructions=(
        "Private Fleet Autopilot control tools for Codex. Read current state before planning. "
        "Upgrade and rollback tools create immutable plans only; the owner must approve those "
        "plans in Fleet Autopilot. This server intentionally exposes no approval tool."
    ),
    json_response=True,
)


def runtime() -> tuple[FleetConfig, FleetStore, TransactionPlanner]:
    config = load_config()
    store = FleetStore(config.state_root)
    return config, store, TransactionPlanner(config, store)


def status_value(config: FleetConfig, store: FleetStore) -> dict[str, Any]:
    snapshot = read_json(config.state_root / "snapshot.json", {})
    transactions = store.list_transactions(20)
    jobs = store.list_jobs(20)
    return {
        "database": {"quick_check": store.quick_check(), "path": str(store.path)},
        "snapshot": {
            "generated_at": snapshot.get("generated_at"),
            "fresh": snapshot.get("fresh"),
            "summary": snapshot.get("summary") or {},
            "hosts": [
                {
                    "id": host.get("id"),
                    "status": host.get("status"),
                    "reachable": host.get("reachable"),
                    "drift": host.get("drift") or [],
                }
                for host in snapshot.get("hosts") or []
            ],
        },
        "active_transactions": [
            item
            for item in transactions
            if item.get("state")
            not in {
                "accepted",
                "rolled_back",
                "manual_intervention_required",
                "cancelled",
            }
        ],
        "active_jobs": [
            item
            for item in jobs
            if item.get("status")
            not in {"complete", "failed", "cancelled", "manual_intervention_required"}
        ],
    }


@mcp.tool()
def fleet_status() -> dict[str, Any]:
    """Return durable database health, the latest host/drift summary, and active work."""
    config, store, _planner = runtime()
    return status_value(config, store)


@mcp.tool()
def fleet_components() -> list[dict[str, Any]]:
    """List every managed component, applicable host, risk class, and adapter qualification."""
    _config, _store, planner = runtime()
    return planner.components()


@mcp.tool()
def fleet_inspect_refresh(target: str = "all") -> dict[str, Any]:
    """Queue a read-only live Fleet inspection and return its durable job ID."""
    config, _store, _planner = runtime()
    return queue_job(config, "inspect", target, requested_by="codex-fleet-control-mcp")


@mcp.tool()
def fleet_job_status(job_id: str, after_sequence: int = 0) -> dict[str, Any]:
    """Read one Fleet job and its ordered, hash-chained events without mutating it."""
    _config, store, _planner = runtime()
    job = store.get_job(job_id)
    if not job:
        raise ValueError(f"Unknown Fleet job: {job_id}")
    return {"job": job, "events": store.events_after(job_id, after_sequence)}


@mcp.tool()
def fleet_transaction_status(transaction_id: str) -> dict[str, Any]:
    """Read an immutable Fleet transaction, approval history, and phase evidence."""
    _config, store, _planner = runtime()
    transaction = store.get_transaction(transaction_id)
    if not transaction:
        raise ValueError(f"Unknown Fleet transaction: {transaction_id}")
    return {"transaction": transaction, "phases": store.phase_runs(transaction_id)}


@mcp.tool()
def fleet_plan_transaction(
    host_id: str,
    component: str,
    action: Literal["upgrade", "rollback"] = "upgrade",
    recovery_point_id: str | None = None,
) -> dict[str, Any]:
    """Create a frozen upgrade/rollback plan. Execution still requires owner approval in Fleet."""
    _config, _store, planner = runtime()
    return planner.plan(
        host_id=host_id,
        component=component,
        action=action,
        requested_by="codex-fleet-control-mcp",
        recovery_point_id=recovery_point_id,
    )


@mcp.tool()
def fleet_recovery_points(
    host_id: str = "", component: str = ""
) -> list[dict[str, Any]]:
    """List verified recovery choices for a host/component before planning a rollback."""
    _config, store, _planner = runtime()
    return store.list_recovery_points(host_id or None, component or None)


@mcp.tool()
def fleet_cancel_transaction(transaction_id: str) -> dict[str, Any]:
    """Cancel before mutation or request cancellation at the next safe boundary after mutation."""
    _config, store, _planner = runtime()
    return store.request_cancellation(transaction_id)


@mcp.tool()
def fleet_benchmarks(limit: int = 20) -> list[dict[str, Any]]:
    """Read recent deterministic and real Codex capability benchmark evidence."""
    _config, store, _planner = runtime()
    return store.list_benchmarks(max(1, min(limit, 100)))


@mcp.tool()
def fleet_run_benchmark(
    suite: Literal["deterministic", "ultra"] = "deterministic",
    host_id: str = "9950x",
) -> dict[str, Any]:
    """Queue a capability benchmark; this does not alter Codex authority or approve upgrades."""
    config, _store, _planner = runtime()
    return queue_job(
        config,
        "benchmark",
        host_id,
        parameters={"suite": suite},
        requested_by="codex-fleet-control-mcp",
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
