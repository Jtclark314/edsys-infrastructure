from __future__ import annotations

import argparse
import json
from pathlib import Path

from .artifact_canary import run_artifact_canary
from .backup import encrypted_backup_and_restore_test
from .benchmark import CapabilityBenchmark
from .collector import FleetCollector
from .config import load_config
from .io import read_json
from .jobs import FleetJobRunner, list_jobs, queue_job
from .store import FleetStore
from .transactions import TransactionPlanner


def main() -> None:
    parser = argparse.ArgumentParser(description="EdSys Fleet Autopilot")
    parser.add_argument("--config")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("collect")
    sub.add_parser("show")
    sub.add_parser("jobs")
    sub.add_parser("components")
    sub.add_parser("transactions")
    transaction = sub.add_parser("transaction")
    transaction.add_argument("transaction_id")
    plan = sub.add_parser("plan")
    plan.add_argument("action", choices=["upgrade", "rollback"])
    plan.add_argument("--host", required=True)
    plan.add_argument("--component", required=True)
    plan.add_argument("--recovery-point")
    plan.add_argument("--requested-by", default="cli")
    approve = sub.add_parser("approve")
    approve.add_argument("transaction_id")
    approve.add_argument("--identity", required=True)
    approve.add_argument("--plan-hash", required=True)
    approve.add_argument("--typed-phrase")
    benchmark = sub.add_parser("benchmark")
    benchmark.add_argument("--suite", choices=["deterministic", "ultra"], required=True)
    benchmark.add_argument("--host", default="9950x")
    benchmark.add_argument("--triggered-by", default="cli")
    artifact_canary = sub.add_parser("artifact-canary")
    artifact_canary.add_argument("--workspace", required=True)
    artifact_canary.add_argument("--retained-dir", required=True)
    artifact_canary.add_argument("--challenge", required=True)
    artifact_canary.add_argument("--spec", required=True)
    sub.add_parser("db-check")
    sub.add_parser("db-backup-restore-test")
    sub.add_parser("gates")
    gate = sub.add_parser("gate-record")
    gate.add_argument("name")
    gate.add_argument("status", choices=["passed", "failed", "pending"])
    gate.add_argument("--verified-by", required=True)
    gate.add_argument("--evidence", default="{}")
    gate.add_argument("--expires-at")
    queue = sub.add_parser("queue")
    queue.add_argument("action", choices=["inspect", "verify", "upgrade", "rollback", "proxmox"])
    queue.add_argument("--target", default="all")
    queue.add_argument("--component", default="")
    queue.add_argument("--parameters", default="{}")
    sub.add_parser("worker-once")
    args = parser.parse_args()
    config = load_config(args.config)
    store = FleetStore(config.state_root)
    if args.command == "collect":
        value = FleetCollector(config).collect()
    elif args.command == "show":
        value = read_json(config.state_root / "snapshot.json", {})
    elif args.command == "jobs":
        value = list_jobs(config)
    elif args.command == "components":
        value = TransactionPlanner(config, store).components()
    elif args.command == "transactions":
        value = store.list_transactions()
    elif args.command == "transaction":
        value = store.get_transaction(args.transaction_id)
    elif args.command == "plan":
        value = TransactionPlanner(config, store).plan(
            host_id=args.host,
            component=args.component,
            action=args.action,
            requested_by=args.requested_by,
            recovery_point_id=args.recovery_point,
        )
    elif args.command == "approve":
        value = store.approve_transaction(
            args.transaction_id,
            identity=args.identity,
            plan_hash=args.plan_hash,
            typed_phrase=args.typed_phrase,
        )
    elif args.command == "benchmark":
        value = CapabilityBenchmark(config).run(args.suite, args.host, args.triggered_by)
    elif args.command == "artifact-canary":
        value = run_artifact_canary(
            Path(args.workspace),
            Path(args.retained_dir),
            args.challenge,
            Path(args.spec),
        )
    elif args.command == "db-check":
        value = {"status": store.quick_check(), "path": str(store.path)}
    elif args.command == "db-backup-restore-test":
        value = encrypted_backup_and_restore_test(config)
    elif args.command == "gates":
        value = store.list_acceptance_gates()
    elif args.command == "gate-record":
        value = store.record_acceptance_gate(
            name=args.name,
            status=args.status,
            verified_by=args.verified_by,
            evidence=json.loads(args.evidence),
            expires_at=args.expires_at,
        )
    elif args.command == "queue":
        value = queue_job(config, args.action, args.target, args.component, json.loads(args.parameters), "cli")
    else:
        value = FleetJobRunner(config).process_one()
    print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
