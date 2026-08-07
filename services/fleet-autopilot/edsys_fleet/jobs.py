from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .adapters import AdapterContext, AdapterRegistry
from .collector import FleetCollector
from .config import FleetConfig
from .io import read_json, utc_now, write_json_atomic
from .proxmox import ProxmoxClient
from .runner import CommandRunner
from .store import MUTATING_ACTIONS, FleetStore, FleetStoreError

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
SUPPORTED_ACTIONS = {"inspect", "verify", "upgrade", "rollback", "proxmox", "benchmark"}


class FleetJobError(RuntimeError):
    pass


class FleetJobCancelled(FleetJobError):
    pass


class FleetJobRunner:
    def __init__(self, config: FleetConfig, runner: CommandRunner | None = None):
        self.config = config
        self.runner = runner or CommandRunner(config.timeout)
        self.collector = FleetCollector(config, self.runner)
        self.proxmox = ProxmoxClient(config, self.runner)
        self.store = FleetStore(config.state_root)
        self.adapters = AdapterRegistry(config)
        self.import_json_history()
        self.reconcile_interrupted_jobs()

    def import_json_history(self) -> None:
        ranks = {"pending": 0, "running": 1, "awaiting-agent": 2, "completed": 3}
        mirrors: dict[str, list[tuple[int, Path, dict[str, Any]]]] = {}
        for status in ranks:
            root = self.config.state_root / "queue" / status
            for path in root.glob("*.json") if root.exists() else []:
                job = read_json(path, {})
                if isinstance(job, dict) and job.get("id"):
                    mirrors.setdefault(str(job["id"]), []).append(
                        (ranks[status], path, job)
                    )
        terminal = {
            "complete",
            "failed",
            "cancelled",
            "manual_intervention_required",
        }
        for job_id, candidates in mirrors.items():
            candidates.sort(key=lambda item: (item[0], str(item[1])), reverse=True)
            selected = candidates[0]
            existing = self.store.get_job(job_id)
            if existing is None:
                self.store.upsert_job(selected[2], compatibility_path=str(selected[1]))
                durable = self.store.get_job(job_id) or selected[2]
            else:
                durable = existing
            completed = next(
                (item for item in candidates if item[0] == ranks["completed"]),
                None,
            )
            if str(durable.get("status")) not in terminal or completed is None:
                continue
            self.store.upsert_job(durable, compatibility_path=str(completed[1]))
            if self.config.compatibility_json_queue:
                for _, path, _ in candidates:
                    if path != completed[1]:
                        path.unlink(missing_ok=True)

    def reconcile_interrupted_jobs(self) -> None:
        running = self.config.state_root / "queue" / "running"
        pending = self.config.state_root / "queue" / "pending"
        completed = self.config.state_root / "queue" / "completed"
        pending.mkdir(parents=True, exist_ok=True)
        completed.mkdir(parents=True, exist_ok=True)
        durable_running = [job for job in self.store.list_jobs(500) if job.get("status") == "running"]
        for job in durable_running:
            path_value = job.get("compatibility_json_path")
            path = Path(path_value) if path_value else running / f"{job['id']}.json"
            if str(job.get("action")) in {"inspect", "verify"}:
                job["status"] = "pending"
                job.pop("started_at", None)
                target = pending / path.name
                if self.config.compatibility_json_queue:
                    write_json_atomic(target, job, mode=0o660)
                    path.unlink(missing_ok=True)
                self.store.update_job(str(job["id"]), state="pending")
                self.store.upsert_job(job, compatibility_path=str(target) if self.config.compatibility_json_queue else None)
                self.store.append_event(
                    str(job["id"]),
                    phase="reconciled",
                    level="warning",
                    progress=0,
                    message="Interrupted read-only job was safely requeued after worker restart.",
                    evidence={"prior_state": "running"},
                    transaction_id=job.get("transaction_id"),
                )
            else:
                phase_runs = self.store.phase_runs(str(job.get("transaction_id") or "")) if job.get("transaction_id") else []
                active = next((item for item in reversed(phase_runs) if item["status"] == "running"), None)
                if not active or active["idempotent"]:
                    job["status"] = "pending"
                    job["parameters"] = {**dict(job.get("parameters") or {}), "resume": True}
                    target = pending / path.name
                    if self.config.compatibility_json_queue:
                        write_json_atomic(target, job, mode=0o660)
                        path.unlink(missing_ok=True)
                    self.store.update_job(str(job["id"]), state="pending")
                    self.store.upsert_job(job, compatibility_path=str(target) if self.config.compatibility_json_queue else None)
                    self.store.append_event(
                        str(job["id"]),
                        phase="reconciled",
                        level="warning",
                        progress=0,
                        message="Interrupted transaction stopped between phases or in an idempotent phase and was safely requeued.",
                        evidence={"active_phase": active},
                        transaction_id=job.get("transaction_id"),
                    )
                    continue
                job.update(
                    {
                        "status": "manual_intervention_required",
                        "completed_at": utc_now(),
                        "error": "Worker restarted during a mutation; external state must be reconciled before retry.",
                    }
                )
                target = completed / path.name
                if self.config.compatibility_json_queue:
                    write_json_atomic(target, job, mode=0o660)
                    path.unlink(missing_ok=True)
                self.store.update_job(str(job["id"]), state="manual_intervention_required", error=job["error"], completed=True)
                self.store.upsert_job(job, compatibility_path=str(target) if self.config.compatibility_json_queue else None)
                self.store.append_event(
                    str(job["id"]),
                    phase="manual_intervention_required",
                    level="error",
                    progress=0,
                    message=job["error"],
                    transaction_id=job.get("transaction_id"),
                )
                if job.get("transaction_id"):
                    transaction = self.store.get_transaction(str(job["transaction_id"]))
                    if transaction and transaction["state"] not in {"manual_intervention_required", "rolled_back", "accepted"}:
                        try:
                            if transaction["state"] != "failed":
                                self.store.transition_transaction(transaction["id"], "failed", error=job["error"])
                            self.store.transition_transaction(
                                transaction["id"], "manual_intervention_required", error=job["error"]
                            )
                        except FleetStoreError:
                            pass

    def process_one(self) -> dict[str, Any] | None:
        pending = self.config.state_root / "queue" / "pending"
        running = self.config.state_root / "queue" / "running"
        pending.mkdir(parents=True, exist_ok=True)
        running.mkdir(parents=True, exist_ok=True)
        job = self.store.claim_next_job()
        if not job:
            return None
        source_value = job.get("compatibility_json_path")
        source = Path(source_value) if source_value else pending / f"{job['id']}.json"
        destination = running / f"{job['id']}.json"
        job.update({"status": "running", "started_at": job.get("started_at") or utc_now()})
        if self.config.compatibility_json_queue:
            if source.exists() and source != destination:
                try:
                    os.replace(source, destination)
                except FileNotFoundError:
                    pass
            write_json_atomic(destination, job, mode=0o660)
        self.store.upsert_job(job, compatibility_path=str(destination) if self.config.compatibility_json_queue else None)
        self.store.append_event(
            str(job["id"]),
            phase="running",
            progress=2,
            message="Fleet worker claimed the job.",
            evidence={"action": job.get("action"), "target": job.get("target"), "component": job.get("component")},
            transaction_id=job.get("transaction_id"),
        )
        locks: list[str] = []
        retain_locks_for_agent = False
        try:
            if str(job.get("action")) in MUTATING_ACTIONS:
                host_lock = f"host:{job.get('target')}"
                self.store.acquire_lock(host_lock, str(job["id"]), ttl_seconds=7200)
                locks.append(host_lock)
                if bool((job.get("parameters") or {}).get("reboot_class")):
                    self.store.acquire_lock("global:reboot-class", str(job["id"]), ttl_seconds=7200)
                    locks.append("global:reboot-class")
            if self._uses_signed_agent(job):
                dispatched = self._dispatch_signed_agent(job)
                retain_locks_for_agent = True
                job.update({"status": "awaiting_agent", "result": dispatched, "error": None})
                self.store.update_job(str(job["id"]), state="awaiting_agent", result=dispatched)
                self.store.append_event(
                    str(job["id"]),
                    phase="awaiting_agent",
                    progress=3,
                    message="Approved job dispatched to the signed outbound Windows agent.",
                    evidence={"agent_command_id": dispatched["id"], "expires_at": dispatched["expires_at"]},
                    transaction_id=job.get("transaction_id"),
                )
                if self.config.compatibility_json_queue:
                    awaiting = self.config.state_root / "queue" / "awaiting-agent"
                    awaiting.mkdir(parents=True, exist_ok=True)
                    target = awaiting / destination.name
                    write_json_atomic(target, job, mode=0o660)
                    destination.unlink(missing_ok=True)
                    self.store.upsert_job(job, compatibility_path=str(target))
                return job
            result = self.execute(job)
            job.update({"status": "complete", "completed_at": utc_now(), "result": result, "error": None})
            self.store.update_job(str(job["id"]), state="complete", result=result, completed=True)
            self.store.append_event(
                str(job["id"]),
                phase="complete",
                progress=100,
                message=str(result.get("message") or "Fleet job completed."),
                evidence=result,
                transaction_id=job.get("transaction_id"),
            )
        except FleetJobCancelled as exc:
            job.update(
                {
                    "status": "cancelled",
                    "completed_at": utc_now(),
                    "result": {"cancelled": True, "safe_boundary": True},
                    "error": None,
                }
            )
            self.store.update_job(
                str(job["id"]), state="cancelled", result=job["result"], completed=True
            )
            self.store.append_event(
                str(job["id"]),
                phase="cancelled",
                level="warning",
                progress=100,
                message=str(exc),
                transaction_id=job.get("transaction_id"),
            )
        except Exception as exc:  # worker must persist a bounded failure result
            job.update({"status": "failed", "completed_at": utc_now(), "result": {}, "error": f"{type(exc).__name__}: {exc}"})
            self.store.update_job(str(job["id"]), state="failed", error=job["error"], completed=True)
            self.store.append_event(
                str(job["id"]),
                phase="failed",
                level="error",
                progress=100,
                message=job["error"],
                transaction_id=job.get("transaction_id"),
            )
        finally:
            if not retain_locks_for_agent:
                self.store.release_locks(str(job["id"]))
        completed = self.config.state_root / "queue" / "completed" / destination.name
        if self.config.compatibility_json_queue:
            write_json_atomic(completed, job, mode=0o660)
            destination.unlink(missing_ok=True)
            self.store.upsert_job(job, compatibility_path=str(completed))
        self._trim_history()
        return job

    def _uses_signed_agent(self, job: dict[str, Any]) -> bool:
        host = next(
            (item for item in self.config.hosts if str(item.get("id")) == str(job.get("target"))),
            None,
        )
        return bool(host and host.get("transport") == "signed-outbound-agent")

    def _dispatch_signed_agent(self, job: dict[str, Any]) -> dict[str, Any]:
        host_id = str(job.get("target") or "")
        with self.store.connect() as connection:
            enrollment = connection.execute(
                "SELECT agent_id FROM agent_enrollments WHERE host_id=? AND state IN ('enrolled','online','dormant')",
                (host_id,),
            ).fetchone()
        if not enrollment:
            raise FleetJobError("Signed outbound agent is not enrolled; approved job remains unexecuted")
        parameters = dict(job.get("parameters") or {})
        transaction = self.store.get_transaction(str(job.get("transaction_id") or "")) if job.get("transaction_id") else None
        if transaction:
            expires_at = str(transaction.get("approval_expires_at") or "")
            if not expires_at:
                raise FleetJobError("Agent mutation has no valid owner approval expiry")
            kind = (
                "node-toolchain-transaction"
                if str(job.get("component")) == "node-toolchain"
                else "guarded-component-transaction"
            )
            manifest = {
                "kind": kind,
                "plan_hash": transaction["plan_hash"],
                "transaction_id": transaction["id"],
                "action": transaction["action"],
                "component": transaction["component"],
                "candidate": transaction.get("candidate"),
                "recovery_point_id": transaction.get("recovery_point_id"),
                "qualification": bool(parameters.get("qualification")),
                **parameters,
            }
            if str(job.get("component")) == "node-toolchain":
                manifest.setdefault("run_id", transaction["id"])
                manifest.setdefault("candidate_version", "24.19.0")
                manifest.setdefault("rollback_version", "24.15.0")
                manifest.setdefault("expected_npm_version", "12.0.2")
        else:
            expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            manifest = {
                "kind": "capability-benchmark" if job.get("action") == "benchmark" else "inventory",
                "action": job.get("action"),
                "component": job.get("component"),
            }
        return self.store.queue_agent_command(
            agent_id=str(enrollment["agent_id"]),
            job_id=str(job["id"]),
            transaction_id=job.get("transaction_id"),
            action=str(job.get("action") or "inspect"),
            component=str(job.get("component") or ""),
            manifest=manifest,
            expires_at=expires_at,
        )

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
        if action == "benchmark":
            from .benchmark import CapabilityBenchmark

            suite = str((job.get("parameters") or {}).get("suite") or "deterministic")
            result = CapabilityBenchmark(self.config, self.runner).run(
                suite, str(job.get("target") or "9950x"), str(job.get("requested_by") or "worker")
            )
            return {
                "message": f"Fleet {suite} capability benchmark completed.",
                "status": result.get("status"),
                "benchmark_run_id": result.get("id"),
                "score": result.get("score"),
                "critical_failures": result.get("critical_failures"),
            }
        if job.get("transaction_id") and action in {"upgrade", "rollback"}:
            return self._execute_transaction(job)
        if action == "upgrade":
            return self._upgrade(job)
        if action == "rollback":
            return self._rollback(job)
        return self._proxmox_action(job)

    def _execute_transaction(self, job: dict[str, Any]) -> dict[str, Any]:
        transaction_id = str(job.get("transaction_id") or "")
        transaction = self.store.get_transaction(transaction_id)
        if not transaction:
            raise FleetJobError("Durable transaction record is missing")
        if transaction["state"] == "cancelled":
            raise FleetJobCancelled("Fleet transaction was cancelled before worker execution.")
        if transaction["state"] not in {
            "approved", "checkpointing", "applying", "restarting", "verifying", "observing"
        }:
            raise FleetJobError(f"Transaction cannot execute from state {transaction['state']}")
        expires = transaction.get("approval_expires_at")
        if expires and datetime.fromisoformat(str(expires)) <= datetime.now(timezone.utc) and transaction["state"] == "approved":
            raise FleetJobError("Owner approval expired before mutation began; a new plan is required")
        policy = self.config.component(str(transaction["component"]))
        adapter_name = str(policy.get("adapter") or "")
        adapter = self.adapters.get(adapter_name)
        parameters = {
            **dict((transaction.get("summary") or {}).get("parameters") or {}),
            **dict(job.get("parameters") or {}),
        }
        context = AdapterContext(
            config=self.config,
            store=self.store,
            runner=self.runner,
            host_id=str(transaction["host_id"]),
            component=str(transaction["component"]),
            transaction_id=transaction_id,
            job_id=str(job["id"]),
            parameters=parameters,
        )
        context.parameters["_fleet_transaction_action"] = str(transaction["action"])
        context.parameters["_fleet_checkpoint_version"] = str(
            transaction.get("current_version") or "unknown"
        )
        qualification = bool(parameters.get("qualification"))
        if not qualification and not self.store.adapter_qualified(
            adapter_name, context.host_id, context.component
        ):
            raise FleetJobError("Adapter has no accepted rollback rehearsal for this host/component")

        progress = {
            "discover": 5, "resolve_candidate": 10, "preflight": 16,
            "checkpoint": 25, "apply": 42, "restart_or_reboot": 52,
            "verify": 68, "accept": 94, "cleanup": 98,
        }
        selected_recovery_point_id: str | None = (
            str(transaction.get("recovery_point_id") or "") or None
        )
        recovery_point_id: str | None = (
            selected_recovery_point_id
            if transaction["action"] == "rollback"
            else (str(parameters.get("recovery_point_id") or "") or None)
        )
        safety_recovery_point_id: str | None = None
        mutation_started = transaction["state"] not in {"approved", "checkpointing"}
        # The ordinary safety target is the pre-transaction checkpoint. A
        # qualification rehearsal briefly changes the last independently verified
        # state: while testing rollback, the already-verified candidate is safer;
        # once reapply begins, the prior checkpoint is safer again.
        automatic_operation = "rollback"
        automatic_verification_target = "rollback"

        try:
            for phase in ("discover", "resolve_candidate", "preflight"):
                self._run_adapter_phase(
                    adapter, context, phase, progress[phase],
                    idempotent=phase in getattr(adapter, "idempotent_phases", set()),
                )

            self._transition_at(transaction_id, "checkpointing", {"approved"})
            checkpoint = self._run_adapter_phase(
                adapter, context, "checkpoint", progress["checkpoint"],
                idempotent="checkpoint" in getattr(adapter, "idempotent_phases", set()),
            )
            if transaction["action"] == "rollback":
                safety_recovery_point_id = checkpoint.recovery_point_id
                if not selected_recovery_point_id or not safety_recovery_point_id:
                    raise FleetJobError(
                        "Rollback requires both the selected recovery point and a fresh safety checkpoint"
                    )
                recovery_point_id = selected_recovery_point_id
                context.parameters["recovery_point_id"] = selected_recovery_point_id
                context.parameters["_fleet_rollback_target"] = "selected"
            else:
                recovery_point_id = checkpoint.recovery_point_id or recovery_point_id
                if recovery_point_id:
                    context.parameters["recovery_point_id"] = recovery_point_id

            self._transition_at(transaction_id, "applying", {"checkpointing"})
            mutation_started = True
            mutation_phase = "rollback" if transaction["action"] == "rollback" else "apply"
            self._run_adapter_phase(
                adapter, context, mutation_phase, progress["apply"],
                idempotent=mutation_phase in getattr(adapter, "idempotent_phases", set()),
                journal_phase="applying" if mutation_phase == "apply" else "rolling_back_selected",
            )

            self._transition_at(transaction_id, "restarting", {"applying"})
            self._run_adapter_phase(
                adapter, context, "restart_or_reboot", progress["restart_or_reboot"],
                idempotent="restart_or_reboot" in getattr(adapter, "idempotent_phases", set()),
            )

            self._transition_at(transaction_id, "verifying", {"restarting", "applying"})
            if transaction["action"] == "rollback":
                context.parameters["_fleet_verification_target"] = "rollback"
            self._run_adapter_phase(
                adapter, context, "verify", progress["verify"],
                idempotent=True,
            )

            if qualification and transaction["action"] == "upgrade":
                self.store.append_event(
                    context.job_id,
                    phase="qualification_rollback",
                    progress=72,
                    message="Beginning the mandatory real rollback rehearsal before qualification.",
                    evidence={"adapter": adapter_name, "recovery_point_id": recovery_point_id},
                    transaction_id=transaction_id,
                )
                automatic_operation = "apply"
                automatic_verification_target = "candidate"
                self._run_adapter_phase(
                    adapter, context, "rollback", 76, idempotent=False,
                    journal_phase="qualification_rollback",
                    allow_repeat=True,
                )
                context.parameters["_fleet_verification_target"] = "rollback"
                self._run_adapter_phase(
                    adapter, context, "verify", 80, idempotent=True,
                    journal_phase="verify_rollback",
                    allow_repeat=True,
                )
                context.parameters["_fleet_verification_target"] = "candidate"
                automatic_operation = "rollback"
                automatic_verification_target = "rollback"
                self._run_adapter_phase(
                    adapter, context, "apply", 84, idempotent=False,
                    journal_phase="qualification_reapply",
                    allow_repeat=True,
                )
                self._run_adapter_phase(
                    adapter, context, "restart_or_reboot", 87, idempotent=True,
                    journal_phase="qualification_restart",
                    allow_repeat=True,
                )
                self._run_adapter_phase(
                    adapter, context, "verify", 90, idempotent=True,
                    journal_phase="qualification_final_verify",
                    allow_repeat=True,
                )

            if bool(policy.get("observation_required")):
                self._transition_at(transaction_id, "observing", {"verifying"})
                self._run_benchmark_gate(context, policy)

            self._run_adapter_phase(adapter, context, "accept", progress["accept"], idempotent=False)
            accepted_checkpoint = (
                safety_recovery_point_id
                if transaction["action"] == "rollback"
                else recovery_point_id
            )
            if accepted_checkpoint:
                self.store.accept_recovery_point(accepted_checkpoint)
            if qualification:
                self.store.qualify_adapter(
                    adapter=adapter_name,
                    host_id=context.host_id,
                    component=context.component,
                    version=str(transaction.get("candidate") or "unknown"),
                    rollback_rehearsed=True,
                    evidence={
                        "transaction_id": transaction_id,
                        "job_id": context.job_id,
                        "recovery_point_id": recovery_point_id,
                        "safety_recovery_point_id": safety_recovery_point_id,
                        "phase_runs": self.store.phase_runs(transaction_id),
                    },
                )
            self._run_adapter_phase(adapter, context, "cleanup", progress["cleanup"], idempotent=True)
            self._transition_at(transaction_id, "accepted", {"verifying", "observing"})
            return {
                "message": "Fleet v2 transaction accepted from observable phase evidence.",
                "transaction_id": transaction_id,
                "adapter": adapter_name,
                "qualification": qualification,
                "recovery_point_id": recovery_point_id,
                "safety_recovery_point_id": safety_recovery_point_id,
                "phase_runs": self.store.phase_runs(transaction_id),
            }
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            cancelled = isinstance(exc, FleetJobCancelled)
            current = self.store.get_transaction(transaction_id) or {}
            if cancelled and not mutation_started:
                if current.get("state") not in {"cancelled", "accepted", "rolled_back"}:
                    try:
                        self.store.transition_transaction(transaction_id, "cancelled", error=error)
                    except FleetStoreError:
                        pass
                raise FleetJobCancelled(
                    "Fleet transaction cancelled before mutation at a verified safe boundary."
                ) from exc
            if current.get("state") not in {"failed", "rollback_pending", "rolling_back", "rolled_back", "manual_intervention_required"}:
                try:
                    self.store.transition_transaction(transaction_id, "failed", error=error)
                except FleetStoreError:
                    pass
            rollback_recovery_point_id = (
                safety_recovery_point_id
                if transaction["action"] == "rollback"
                else recovery_point_id
            )
            if not mutation_started or not rollback_recovery_point_id:
                try:
                    self.store.transition_transaction(transaction_id, "manual_intervention_required", error=error)
                except FleetStoreError:
                    pass
                raise
            try:
                self.store.transition_transaction(transaction_id, "rollback_pending", error=error)
                self.store.transition_transaction(transaction_id, "rolling_back", error=error)
                context.parameters["recovery_point_id"] = rollback_recovery_point_id
                if transaction["action"] == "rollback":
                    context.parameters["_fleet_rollback_target"] = "safety"
                    context.parameters["_fleet_verification_target"] = "candidate"
                    automatic_operation = "rollback"
                    automatic_verification_target = "candidate"
                else:
                    context.parameters["_fleet_rollback_target"] = "rollback"
                    context.parameters["_fleet_verification_target"] = (
                        automatic_verification_target
                    )
                self._run_adapter_phase(
                    adapter, context, automatic_operation, 96, idempotent=False,
                    journal_phase="automatic_rollback", allow_repeat=True,
                    honor_cancellation=False,
                )
                # Both apply and rollback can require a service/process refresh
                # before their acceptance predicate is meaningful.
                self._run_adapter_phase(
                    adapter,
                    context,
                    "restart_or_reboot",
                    97,
                    idempotent="restart_or_reboot"
                    in getattr(adapter, "idempotent_phases", set()),
                    journal_phase="automatic_rollback_restart",
                    allow_repeat=True,
                    honor_cancellation=False,
                )
                self._run_adapter_phase(
                    adapter,
                    context,
                    "verify",
                    98,
                    idempotent=True,
                    journal_phase="automatic_rollback_verify",
                    allow_repeat=True,
                    honor_cancellation=False,
                )
                if automatic_operation == "rollback":
                    self.store.accept_recovery_point(rollback_recovery_point_id)
                self.store.transition_transaction(transaction_id, "rolled_back", error=error)
            except Exception as rollback_exc:
                combined = f"{error}; automatic rollback failed: {type(rollback_exc).__name__}: {rollback_exc}"
                try:
                    self.store.transition_transaction(
                        transaction_id, "manual_intervention_required", error=combined
                    )
                except FleetStoreError:
                    pass
                raise FleetJobError(combined) from rollback_exc
            if cancelled:
                raise FleetJobCancelled(
                    "Fleet transaction cancellation reached a safe boundary and automatic component rollback completed."
                ) from exc
            raise FleetJobError(f"{error}; automatic component rollback completed") from exc

    def _run_adapter_phase(
        self,
        adapter: Any,
        context: AdapterContext,
        phase: str,
        progress: float,
        *,
        idempotent: bool,
        journal_phase: str | None = None,
        allow_repeat: bool = False,
        honor_cancellation: bool = True,
    ):
        if honor_cancellation:
            transaction = self.store.get_transaction(context.transaction_id)
            if transaction and (
                transaction.get("cancellation_requested") or transaction.get("state") == "cancelled"
            ):
                raise FleetJobCancelled("Owner cancellation requested; stopping at this phase boundary.")
        prior = [item for item in self.store.phase_runs(context.transaction_id) if item["phase"] == (journal_phase or phase)]
        if not allow_repeat:
            passed = next((item for item in reversed(prior) if item["status"] == "passed"), None)
            if passed:
                from .adapters import AdapterResult

                return AdapterResult(
                    status="passed",
                    message=f"Resumed after previously completed {phase} phase.",
                    evidence=passed["evidence"],
                    recovery_point_id=passed["evidence"].get("recovery_point_id"),
                    resumable=idempotent,
                )
        phase_key = journal_phase or phase
        running = next((item for item in reversed(prior) if item["status"] == "running"), None)
        if running and not running["idempotent"]:
            raise FleetJobError(
                f"Worker interruption left non-idempotent phase {phase_key} externally inconclusive"
            )
        phase_run = self.store.start_phase(context.transaction_id, phase_key, idempotent=idempotent)
        self.store.append_event(
            context.job_id,
            phase=phase_key,
            progress=progress,
            message=f"Starting adapter phase {phase_key}.",
            evidence={"adapter": adapter.name, "attempt": phase_run["attempt"], "idempotent": idempotent},
            transaction_id=context.transaction_id,
        )
        try:
            result = getattr(adapter, phase)(context)
            if result.status != "passed":
                raise FleetJobError(result.message)
            evidence = {**dict(result.evidence or {})}
            if result.recovery_point_id:
                evidence["recovery_point_id"] = result.recovery_point_id
            self.store.finish_phase(
                context.transaction_id,
                phase_key,
                int(phase_run["attempt"]),
                status="passed",
                evidence=evidence,
            )
            self.store.append_event(
                context.job_id,
                phase=phase_key,
                progress=progress,
                message=result.message,
                evidence=evidence,
                transaction_id=context.transaction_id,
            )
            return result
        except Exception as exc:
            self.store.finish_phase(
                context.transaction_id,
                phase_key,
                int(phase_run["attempt"]),
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

    def _transition_at(self, transaction_id: str, new_state: str, allowed: set[str]) -> None:
        current = self.store.get_transaction(transaction_id)
        if not current:
            raise FleetJobError("Transaction disappeared during execution")
        if current["state"] == new_state:
            return
        if current["state"] in allowed:
            self.store.transition_transaction(transaction_id, new_state)

    def _run_benchmark_gate(self, context: AdapterContext, policy: dict[str, Any]) -> None:
        from .benchmark import CapabilityBenchmark

        result = CapabilityBenchmark(self.config, self.runner).run(
            "deterministic", context.host_id, "transaction-gate"
        )
        self.store.append_event(
            context.job_id,
            phase="observing",
            progress=92,
            message="Affected deterministic capability benchmark completed.",
            evidence={"run_id": result.get("id"), "score": result.get("score"), "status": result.get("status")},
            transaction_id=context.transaction_id,
        )
        if result.get("status") != "passed":
            raise FleetJobError("Affected capability benchmark failed; component acceptance is blocked")

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
        raise FleetJobError(
            f"Component adapter {component} is not yet qualified; no mutation was attempted. "
            "Create a v2 transaction and complete its rollback rehearsal first."
        )

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


def queue_job(config: FleetConfig, action: str, target: str = "all", component: str = "", parameters: dict[str, Any] | None = None, requested_by: str = "portal", transaction_id: str | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
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
        "transaction_id": transaction_id,
        "idempotency_key": idempotency_key,
    }
    path = config.state_root / "queue" / "pending" / f"{job_id}.json"
    if config.compatibility_json_queue:
        write_json_atomic(path, job, mode=0o660)
    FleetStore(config.state_root).upsert_job(job, compatibility_path=str(path) if config.compatibility_json_queue else None)
    return job


def list_jobs(config: FleetConfig, limit: int = 40) -> list[dict[str, Any]]:
    database = config.state_root / "fleet-control.sqlite"
    if database.exists():
        return FleetStore(config.state_root).list_jobs(limit)
    jobs: list[dict[str, Any]] = []
    for status in ("running", "pending", "completed"):
        root = config.state_root / "queue" / status
        for path in root.glob("*.json") if root.exists() else []:
            value = read_json(path, {})
            if isinstance(value, dict):
                jobs.append(value)
    return sorted(jobs, key=lambda job: str(job.get("requested_at") or ""), reverse=True)[:limit]
