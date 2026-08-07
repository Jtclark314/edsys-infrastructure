from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 4
MAX_MESSAGE_BYTES = 2048
MAX_EVIDENCE_BYTES = 16384
MAX_PLAN_BYTES = 262144
TERMINAL_TRANSACTION_STATES = {
    "accepted",
    "rolled_back",
    "manual_intervention_required",
    "cancelled",
}
TRANSACTION_TRANSITIONS = {
    "planned": {"preflight", "cancelled"},
    "preflight": {"awaiting_approval", "failed", "cancelled"},
    "awaiting_approval": {"approved", "cancelled"},
    "approved": {"checkpointing", "cancelled"},
    "checkpointing": {"applying", "failed", "cancelled"},
    "applying": {"restarting", "verifying", "failed"},
    "restarting": {"verifying", "failed"},
    "verifying": {"observing", "accepted", "failed"},
    "observing": {"accepted", "failed"},
    "failed": {"rollback_pending", "manual_intervention_required"},
    "rollback_pending": {"rolling_back", "manual_intervention_required"},
    "rolling_back": {"rolled_back", "manual_intervention_required"},
}
MUTATING_ACTIONS = {"upgrade", "rollback", "proxmox", "benchmark", "benchmark-infrastructure"}
SECRET_KEY = re.compile(
    r"(?i)(?:^|_)(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|"
    r"authorization|cookie|set[_-]?cookie|prompt|environment|env)(?:$|_)"
)
BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
LIKELY_SECRET = re.compile(
    r"(?i)\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|"
    r"github_pat_[A-Za-z0-9_]{12,})\b"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sanitize_message(value: Any) -> str:
    text = str(value or "")
    text = BEARER.sub("Bearer [REDACTED]", text)
    text = LIKELY_SECRET.sub("[REDACTED]", text)
    encoded = text.encode("utf-8", errors="replace")[:MAX_MESSAGE_BYTES]
    return encoded.decode("utf-8", errors="ignore")


def sanitize_evidence(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:80]:
            key = str(raw_key)[:96]
            output[key] = "[REDACTED]" if SECRET_KEY.search(key) else sanitize_evidence(
                raw_value, depth=depth + 1
            )
        value = output
    elif isinstance(value, (list, tuple)):
        value = [sanitize_evidence(item, depth=depth + 1) for item in list(value)[:80]]
    elif isinstance(value, str):
        value = sanitize_message(value)
    elif value is not None and not isinstance(value, (bool, int, float)):
        value = sanitize_message(value)
    encoded = canonical_json(value).encode("utf-8")
    if len(encoded) > MAX_EVIDENCE_BYTES:
        return {
            "truncated": True,
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "original_bytes": len(encoded),
        }
    return value


def sanitize_plan(value: Any, *, depth: int = 0) -> Any:
    """Validate an executable plan without truncating or silently changing intent."""

    if depth > 10:
        raise FleetStoreError("Executable plan nesting is too deep")
    if isinstance(value, dict):
        if len(value) > 256:
            raise FleetStoreError("Executable plan has too many fields")
        output: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if len(key) > 128 or SECRET_KEY.search(key):
                raise FleetStoreError(f"Executable plan contains a forbidden field: {key[:40]}")
            output[key] = sanitize_plan(raw_value, depth=depth + 1)
        value = output
    elif isinstance(value, (list, tuple)):
        if len(value) > 1024:
            raise FleetStoreError("Executable plan list is too large")
        value = [sanitize_plan(item, depth=depth + 1) for item in value]
    elif isinstance(value, str):
        if len(value.encode("utf-8")) > 8192:
            raise FleetStoreError("Executable plan string is too large")
        if BEARER.search(value) or LIKELY_SECRET.search(value):
            raise FleetStoreError("Executable plan contains credential-like material")
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise FleetStoreError("Executable plan contains an unsupported value")
    if depth == 0 and len(canonical_json(value).encode("utf-8")) > MAX_PLAN_BYTES:
        raise FleetStoreError("Executable plan exceeds the durable size limit")
    return value


class FleetStoreError(RuntimeError):
    pass


class FleetStore:
    """Durable Fleet v2 state, audit journal, recovery, and benchmark store."""

    def __init__(self, state_root: Path | str):
        self.state_root = Path(state_root)
        self.state_root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.state_root, 0o2770)
        except PermissionError:
            pass
        self.path = self.state_root / "fleet-control.sqlite"
        self._migrate()

    @contextmanager
    def connect(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            if immediate:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _migrate(self) -> None:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("BEGIN IMMEDIATE")
        try:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current > SCHEMA_VERSION:
                raise FleetStoreError(
                    f"Fleet database schema {current} is newer than supported {SCHEMA_VERSION}"
                )
            if current < 1:
                connection.executescript(
                    """
                    CREATE TABLE transactions (
                      id TEXT PRIMARY KEY,
                      plan_hash TEXT NOT NULL UNIQUE,
                      host_id TEXT NOT NULL,
                      component TEXT NOT NULL,
                      action TEXT NOT NULL,
                      risk_class TEXT NOT NULL,
                      state TEXT NOT NULL,
                      current_version TEXT,
                      candidate TEXT,
                      policy_version TEXT NOT NULL,
                      preflight_hash TEXT,
                      recovery_point_id TEXT,
                      requested_by TEXT NOT NULL,
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL,
                      approval_expires_at TEXT,
                      cancellation_requested INTEGER NOT NULL DEFAULT 0,
                      error TEXT,
                      summary_json TEXT NOT NULL
                    );
                    CREATE TABLE jobs (
                      id TEXT PRIMARY KEY,
                      transaction_id TEXT REFERENCES transactions(id),
                      action TEXT NOT NULL,
                      host_id TEXT NOT NULL,
                      component TEXT NOT NULL DEFAULT '',
                      state TEXT NOT NULL,
                      idempotency_key TEXT,
                      requested_by TEXT NOT NULL,
                      requested_at TEXT NOT NULL,
                      started_at TEXT,
                      completed_at TEXT,
                      result_json TEXT NOT NULL DEFAULT '{}',
                      error TEXT,
                      compatibility_json_path TEXT,
                      UNIQUE(host_id, component, idempotency_key)
                    );
                    CREATE TABLE job_events (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      event_id TEXT NOT NULL UNIQUE,
                      sequence INTEGER NOT NULL,
                      job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                      transaction_id TEXT,
                      timestamp TEXT NOT NULL,
                      phase TEXT NOT NULL,
                      level TEXT NOT NULL,
                      progress REAL NOT NULL,
                      message TEXT NOT NULL,
                      evidence_json TEXT NOT NULL,
                      previous_hash TEXT NOT NULL,
                      event_hash TEXT NOT NULL UNIQUE,
                      UNIQUE(job_id, sequence)
                    );
                    CREATE TABLE approvals (
                      id TEXT PRIMARY KEY,
                      transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
                      identity TEXT NOT NULL,
                      decision TEXT NOT NULL,
                      plan_hash TEXT NOT NULL,
                      preflight_hash TEXT,
                      recovery_point_id TEXT,
                      decided_at TEXT NOT NULL,
                      expires_at TEXT NOT NULL,
                      typed_phrase_verified INTEGER NOT NULL,
                      result TEXT NOT NULL
                    );
                    CREATE TABLE recovery_points (
                      id TEXT PRIMARY KEY,
                      host_id TEXT NOT NULL,
                      component TEXT NOT NULL,
                      version TEXT NOT NULL,
                      checksum TEXT NOT NULL,
                      checkpoint_type TEXT NOT NULL,
                      artifact_ref TEXT NOT NULL,
                      metadata_json TEXT NOT NULL,
                      compatible INTEGER NOT NULL,
                      verified INTEGER NOT NULL,
                      accepted INTEGER NOT NULL,
                      created_at TEXT NOT NULL,
                      verified_at TEXT,
                      last_working INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE TABLE adapter_qualifications (
                      adapter TEXT NOT NULL,
                      host_id TEXT NOT NULL,
                      component TEXT NOT NULL,
                      version TEXT NOT NULL,
                      status TEXT NOT NULL,
                      rollback_rehearsed INTEGER NOT NULL,
                      evidence_json TEXT NOT NULL,
                      qualified_at TEXT,
                      PRIMARY KEY(adapter, host_id, component)
                    );
                    CREATE TABLE benchmark_runs (
                      id TEXT PRIMARY KEY,
                      suite TEXT NOT NULL,
                      contract_version TEXT NOT NULL,
                      host_id TEXT NOT NULL,
                      status TEXT NOT NULL,
                      score REAL NOT NULL DEFAULT 0,
                      critical_failures INTEGER NOT NULL DEFAULT 0,
                      started_at TEXT NOT NULL,
                      completed_at TEXT,
                      triggered_by TEXT NOT NULL,
                      summary_json TEXT NOT NULL DEFAULT '{}'
                    );
                    CREATE TABLE benchmark_results (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      run_id TEXT NOT NULL REFERENCES benchmark_runs(id) ON DELETE CASCADE,
                      category TEXT NOT NULL,
                      probe TEXT NOT NULL,
                      status TEXT NOT NULL,
                      critical INTEGER NOT NULL,
                      elapsed_ms INTEGER NOT NULL,
                      evidence_json TEXT NOT NULL,
                      artifact_ref TEXT,
                      cleanup_status TEXT NOT NULL,
                      UNIQUE(run_id, category, probe)
                    );
                    CREATE TABLE agent_enrollments (
                      agent_id TEXT PRIMARY KEY,
                      host_id TEXT NOT NULL UNIQUE,
                      public_key TEXT NOT NULL,
                      fingerprint TEXT NOT NULL UNIQUE,
                      state TEXT NOT NULL,
                      enrolled_at TEXT NOT NULL,
                      last_nonce TEXT,
                      last_seen_at TEXT,
                      metadata_json TEXT NOT NULL
                    );
                    CREATE TABLE agent_heartbeats (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      agent_id TEXT NOT NULL REFERENCES agent_enrollments(agent_id),
                      nonce TEXT NOT NULL,
                      received_at TEXT NOT NULL,
                      body_hash TEXT NOT NULL,
                      status_json TEXT NOT NULL,
                      UNIQUE(agent_id, nonce)
                    );
                    CREATE TABLE mutation_locks (
                      name TEXT PRIMARY KEY,
                      owner_job_id TEXT NOT NULL REFERENCES jobs(id),
                      acquired_at TEXT NOT NULL,
                      expires_at TEXT NOT NULL
                    );
                    CREATE TABLE imports (
                      source TEXT PRIMARY KEY,
                      imported_at TEXT NOT NULL,
                      source_hash TEXT NOT NULL
                    );
                    """
                )
                connection.execute("PRAGMA user_version=1")
            if current < 2:
                connection.executescript(
                    """
                    CREATE INDEX IF NOT EXISTS idx_transactions_state_updated
                      ON transactions(state, updated_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_jobs_state_requested
                      ON jobs(state, requested_at);
                    CREATE INDEX IF NOT EXISTS idx_events_job_sequence
                      ON job_events(job_id, sequence);
                    CREATE INDEX IF NOT EXISTS idx_recovery_host_component
                      ON recovery_points(host_id, component, accepted DESC, created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_benchmark_host_started
                      ON benchmark_runs(host_id, started_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_heartbeats_agent_received
                      ON agent_heartbeats(agent_id, received_at DESC);
                    """
                )
                connection.execute("PRAGMA user_version=2")
            if current < 3:
                connection.executescript(
                    """
                    ALTER TABLE jobs ADD COLUMN parameters_json TEXT NOT NULL DEFAULT '{}';
                    CREATE TABLE adapter_phase_runs (
                      transaction_id TEXT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
                      phase TEXT NOT NULL,
                      attempt INTEGER NOT NULL,
                      status TEXT NOT NULL,
                      idempotent INTEGER NOT NULL,
                      started_at TEXT NOT NULL,
                      completed_at TEXT,
                      evidence_json TEXT NOT NULL DEFAULT '{}',
                      external_ref TEXT,
                      error TEXT,
                      PRIMARY KEY(transaction_id, phase, attempt)
                    );
                    CREATE INDEX idx_adapter_phase_transaction
                      ON adapter_phase_runs(transaction_id, started_at, attempt);
                    CREATE TABLE agent_commands (
                      id TEXT PRIMARY KEY,
                      agent_id TEXT NOT NULL REFERENCES agent_enrollments(agent_id),
                      job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
                      transaction_id TEXT REFERENCES transactions(id),
                      action TEXT NOT NULL,
                      component TEXT NOT NULL,
                      manifest_json TEXT NOT NULL,
                      manifest_hash TEXT NOT NULL,
                      state TEXT NOT NULL,
                      queued_at TEXT NOT NULL,
                      not_before TEXT,
                      expires_at TEXT NOT NULL,
                      delivered_at TEXT,
                      completed_at TEXT,
                      result_json TEXT NOT NULL DEFAULT '{}',
                      error TEXT
                    );
                    CREATE INDEX idx_agent_commands_delivery
                      ON agent_commands(agent_id, state, queued_at);
                    """
                )
                connection.execute("PRAGMA user_version=3")
            if current < 4:
                connection.executescript(
                    """
                    CREATE TABLE acceptance_gates (
                      name TEXT PRIMARY KEY,
                      status TEXT NOT NULL,
                      evidence_json TEXT NOT NULL,
                      verified_by TEXT NOT NULL,
                      verified_at TEXT NOT NULL,
                      expires_at TEXT
                    );
                    CREATE INDEX idx_acceptance_gates_status
                      ON acceptance_gates(status, verified_at DESC);
                    """
                )
                connection.execute("PRAGMA user_version=4")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        try:
            os.chmod(self.path, 0o660)
        except PermissionError:
            pass

    def upsert_job(self, job: dict[str, Any], *, compatibility_path: str | None = None) -> None:
        with self.connect(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                  id, transaction_id, action, host_id, component, state,
                  idempotency_key, requested_by, requested_at, started_at,
                  completed_at, result_json, error, compatibility_json_path,
                  parameters_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  transaction_id=excluded.transaction_id,
                  state=excluded.state,
                  started_at=COALESCE(excluded.started_at, jobs.started_at),
                  completed_at=COALESCE(excluded.completed_at, jobs.completed_at),
                  result_json=excluded.result_json,
                  error=excluded.error,
                  compatibility_json_path=COALESCE(excluded.compatibility_json_path, jobs.compatibility_json_path),
                  parameters_json=excluded.parameters_json
                """,
                (
                    str(job["id"]),
                    job.get("transaction_id"),
                    str(job.get("action") or "inspect"),
                    str(job.get("target") or job.get("host_id") or "all"),
                    str(job.get("component") or ""),
                    str(job.get("status") or job.get("state") or "pending"),
                    job.get("idempotency_key"),
                    str(job.get("requested_by") or "unknown"),
                    str(job.get("requested_at") or utc_now()),
                    job.get("started_at"),
                    job.get("completed_at"),
                    canonical_json(sanitize_evidence(job.get("result") or {})),
                    sanitize_message(job.get("error")) or None,
                    compatibility_path,
                    canonical_json(sanitize_plan(job.get("parameters") or {})),
                ),
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._job_row(row) if row else None

    def list_jobs(self, limit: int = 80) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY requested_at DESC LIMIT ?", (max(1, min(limit, 500)),)
            ).fetchall()
        return [self._job_row(row) for row in rows]

    @staticmethod
    def _job_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "transaction_id": row["transaction_id"],
            "action": row["action"],
            "target": row["host_id"],
            "component": row["component"],
            "status": row["state"],
            "idempotency_key": row["idempotency_key"],
            "requested_by": row["requested_by"],
            "requested_at": row["requested_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "result": json.loads(row["result_json"] or "{}"),
            "error": row["error"],
            "parameters": json.loads(row["parameters_json"] or "{}"),
            "compatibility_json_path": row["compatibility_json_path"],
        }

    def claim_next_job(self) -> dict[str, Any] | None:
        """Atomically claim the oldest durable pending job.

        SQLite is authoritative. The JSON path, when present, is only a one-release
        compatibility mirror and is never used to decide ownership.
        """
        with self.connect(immediate=True) as connection:
            row = connection.execute(
                "SELECT id FROM jobs WHERE state='pending' ORDER BY requested_at, id LIMIT 1"
            ).fetchone()
            if not row:
                return None
            started = utc_now()
            updated = connection.execute(
                "UPDATE jobs SET state='running', started_at=COALESCE(started_at, ?) "
                "WHERE id=? AND state='pending'",
                (started, row["id"]),
            )
            if updated.rowcount != 1:
                return None
            claimed = connection.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
        return self._job_row(claimed) if claimed else None

    def update_job(
        self,
        job_id: str,
        *,
        state: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        completed: bool = False,
    ) -> dict[str, Any]:
        with self.connect(immediate=True) as connection:
            connection.execute(
                "UPDATE jobs SET state=?, result_json=?, error=?, completed_at=? WHERE id=?",
                (
                    state,
                    canonical_json(sanitize_evidence(result or {})),
                    sanitize_message(error) or None,
                    utc_now() if completed else None,
                    job_id,
                ),
            )
        value = self.get_job(job_id)
        if not value:
            raise FleetStoreError("Unknown job")
        return value

    def append_event(
        self,
        job_id: str,
        *,
        phase: str,
        message: str,
        level: str = "info",
        progress: float = 0,
        evidence: Any = None,
        transaction_id: str | None = None,
    ) -> dict[str, Any]:
        safe_message = sanitize_message(message)
        safe_evidence = sanitize_evidence(evidence or {})
        timestamp = utc_now()
        with self.connect(immediate=True) as connection:
            prior = connection.execute(
                "SELECT sequence, event_hash FROM job_events WHERE job_id=? ORDER BY sequence DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            sequence = int(prior["sequence"] if prior else 0) + 1
            previous_hash = str(prior["event_hash"] if prior else "0" * 64)
            event_id = f"evt-{uuid.uuid4().hex}"
            body = {
                "event_id": event_id,
                "sequence": sequence,
                "job_id": job_id,
                "transaction_id": transaction_id,
                "timestamp": timestamp,
                "phase": str(phase)[:64],
                "level": str(level)[:16],
                "progress": round(max(0.0, min(float(progress), 100.0)), 2),
                "message": safe_message,
                "evidence": safe_evidence,
                "previous_hash": previous_hash,
            }
            event_hash = hashlib.sha256(canonical_json(body).encode()).hexdigest()
            connection.execute(
                """
                INSERT INTO job_events (
                  event_id, sequence, job_id, transaction_id, timestamp, phase,
                  level, progress, message, evidence_json, previous_hash, event_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    sequence,
                    job_id,
                    transaction_id,
                    timestamp,
                    body["phase"],
                    body["level"],
                    body["progress"],
                    safe_message,
                    canonical_json(safe_evidence),
                    previous_hash,
                    event_hash,
                ),
            )
        return {**body, "event_hash": event_hash}

    def events_after(self, job_id: str, after_sequence: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM job_events WHERE job_id=? AND sequence>?
                ORDER BY sequence ASC LIMIT ?
                """,
                (job_id, max(0, after_sequence), max(1, min(limit, 2000))),
            ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "sequence": row["sequence"],
                "job_id": row["job_id"],
                "transaction_id": row["transaction_id"],
                "timestamp": row["timestamp"],
                "phase": row["phase"],
                "level": row["level"],
                "progress": row["progress"],
                "message": row["message"],
                "evidence": json.loads(row["evidence_json"]),
                "previous_hash": row["previous_hash"],
                "event_hash": row["event_hash"],
            }
            for row in rows
        ]

    def create_transaction(self, plan: dict[str, Any]) -> dict[str, Any]:
        normalized = {
            "host_id": str(plan["host_id"]),
            "component": str(plan["component"]),
            "action": str(plan.get("action") or "upgrade"),
            "risk_class": str(plan.get("risk_class") or "ordinary"),
            "current_version": str(plan.get("current_version") or "unknown"),
            "candidate": str(plan.get("candidate") or "unknown"),
            "policy_version": str(plan.get("policy_version") or "2"),
            "preflight_hash": plan.get("preflight_hash"),
            "recovery_point_id": plan.get("recovery_point_id"),
            "requested_by": str(plan.get("requested_by") or "unknown"),
            "operations": sanitize_plan(plan.get("operations") or []),
            "parameters": sanitize_plan(plan.get("parameters") or {}),
        }
        plan_hash = hashlib.sha256(canonical_json(normalized).encode()).hexdigest()
        transaction_id = f"txn-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:10]}"
        created = utc_now()
        summary = {**normalized, "plan_hash": plan_hash}
        with self.connect(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO transactions (
                  id, plan_hash, host_id, component, action, risk_class, state,
                  current_version, candidate, policy_version, preflight_hash,
                  recovery_point_id, requested_by, created_at, updated_at, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, 'planned', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transaction_id,
                    plan_hash,
                    normalized["host_id"],
                    normalized["component"],
                    normalized["action"],
                    normalized["risk_class"],
                    normalized["current_version"],
                    normalized["candidate"],
                    normalized["policy_version"],
                    normalized["preflight_hash"],
                    normalized["recovery_point_id"],
                    normalized["requested_by"],
                    created,
                    created,
                    canonical_json(summary),
                ),
            )
        return self.get_transaction(transaction_id) or {}

    def get_transaction(self, transaction_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM transactions WHERE id=?", (transaction_id,)
            ).fetchone()
            if not row:
                return None
            approvals = connection.execute(
                "SELECT * FROM approvals WHERE transaction_id=? ORDER BY decided_at DESC",
                (transaction_id,),
            ).fetchall()
            jobs = connection.execute(
                "SELECT id FROM jobs WHERE transaction_id=? ORDER BY requested_at",
                (transaction_id,),
            ).fetchall()
        value = dict(row)
        value["summary"] = json.loads(value.pop("summary_json") or "{}")
        value["cancellation_requested"] = bool(value["cancellation_requested"])
        value["approvals"] = [dict(item) for item in approvals]
        value["job_ids"] = [item["id"] for item in jobs]
        return value

    def list_transactions(self, limit: int = 80) -> list[dict[str, Any]]:
        with self.connect() as connection:
            ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM transactions ORDER BY created_at DESC LIMIT ?",
                    (max(1, min(limit, 500)),),
                ).fetchall()
            ]
        return [value for value in (self.get_transaction(item) for item in ids) if value]

    def transition_transaction(
        self, transaction_id: str, new_state: str, *, error: str | None = None
    ) -> dict[str, Any]:
        with self.connect(immediate=True) as connection:
            row = connection.execute(
                "SELECT state FROM transactions WHERE id=?", (transaction_id,)
            ).fetchone()
            if not row:
                raise FleetStoreError("Unknown transaction")
            current = str(row["state"])
            if new_state != current and new_state not in TRANSACTION_TRANSITIONS.get(current, set()):
                raise FleetStoreError(f"Invalid transaction transition: {current} -> {new_state}")
            connection.execute(
                "UPDATE transactions SET state=?, updated_at=?, error=? WHERE id=?",
                (new_state, utc_now(), sanitize_message(error) or None, transaction_id),
            )
        return self.get_transaction(transaction_id) or {}

    def approve_transaction(
        self,
        transaction_id: str,
        *,
        identity: str,
        plan_hash: str,
        typed_phrase: str | None,
        ttl_minutes: int = 15,
    ) -> dict[str, Any]:
        with self.connect(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM transactions WHERE id=?", (transaction_id,)
            ).fetchone()
            if not row or row["state"] != "awaiting_approval":
                raise FleetStoreError("Transaction is not awaiting approval")
            if not secrets.compare_digest(str(row["plan_hash"]), str(plan_hash)):
                raise FleetStoreError("Plan hash changed; approval is invalid")
            typed_required = row["risk_class"] in {"reboot", "driver", "windows-update", "proxmox-host"}
            required = f"APPROVE {str(plan_hash)[-8:]}"
            typed_ok = not typed_required or typed_phrase == required
            if not typed_ok:
                raise FleetStoreError(f"Typed approval must exactly match {required}")
            expires = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()
            approval_id = f"approval-{uuid.uuid4().hex}"
            connection.execute(
                """
                INSERT INTO approvals (
                  id, transaction_id, identity, decision, plan_hash, preflight_hash,
                  recovery_point_id, decided_at, expires_at, typed_phrase_verified, result
                ) VALUES (?, ?, ?, 'approved', ?, ?, ?, ?, ?, ?, 'valid')
                """,
                (
                    approval_id,
                    transaction_id,
                    sanitize_message(identity),
                    plan_hash,
                    row["preflight_hash"],
                    row["recovery_point_id"],
                    utc_now(),
                    expires,
                    int(typed_ok),
                ),
            )
            connection.execute(
                "UPDATE transactions SET state='approved', updated_at=?, approval_expires_at=? WHERE id=?",
                (utc_now(), expires, transaction_id),
            )
        return self.get_transaction(transaction_id) or {}

    def request_cancellation(self, transaction_id: str) -> dict[str, Any]:
        with self.connect(immediate=True) as connection:
            row = connection.execute(
                "SELECT state FROM transactions WHERE id=?", (transaction_id,)
            ).fetchone()
            if not row:
                raise FleetStoreError("Unknown transaction")
            state = str(row["state"])
            if state in {"planned", "preflight", "awaiting_approval", "approved"}:
                connection.execute(
                    "UPDATE transactions SET state='cancelled', updated_at=? WHERE id=?",
                    (utc_now(), transaction_id),
                )
            elif state not in TERMINAL_TRANSACTION_STATES:
                connection.execute(
                    "UPDATE transactions SET cancellation_requested=1, updated_at=? WHERE id=?",
                    (utc_now(), transaction_id),
                )
        return self.get_transaction(transaction_id) or {}

    def acquire_lock(self, name: str, job_id: str, *, ttl_seconds: int = 3600) -> None:
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
        with self.connect(immediate=True) as connection:
            connection.execute("DELETE FROM mutation_locks WHERE expires_at<?", (now.isoformat(),))
            try:
                result = connection.execute(
                    "INSERT INTO mutation_locks(name, owner_job_id, acquired_at, expires_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
                    "acquired_at=excluded.acquired_at, expires_at=excluded.expires_at "
                    "WHERE mutation_locks.owner_job_id=excluded.owner_job_id",
                    (name, job_id, now.isoformat(), expires),
                )
                if result.rowcount != 1:
                    raise FleetStoreError(f"Mutation lock is held: {name}")
            except sqlite3.IntegrityError as exc:
                raise FleetStoreError(f"Mutation lock is held: {name}") from exc

    def release_locks(self, job_id: str) -> None:
        with self.connect(immediate=True) as connection:
            connection.execute("DELETE FROM mutation_locks WHERE owner_job_id=?", (job_id,))

    def start_phase(
        self,
        transaction_id: str,
        phase: str,
        *,
        idempotent: bool,
    ) -> dict[str, Any]:
        with self.connect(immediate=True) as connection:
            active = connection.execute(
                "SELECT * FROM adapter_phase_runs WHERE transaction_id=? AND phase=? "
                "AND status='running' ORDER BY attempt DESC LIMIT 1",
                (transaction_id, phase),
            ).fetchone()
            if active:
                return self._phase_row(active)
            prior = connection.execute(
                "SELECT COALESCE(MAX(attempt), 0) AS attempt FROM adapter_phase_runs "
                "WHERE transaction_id=? AND phase=?",
                (transaction_id, phase),
            ).fetchone()
            attempt = int(prior["attempt"] or 0) + 1
            connection.execute(
                "INSERT INTO adapter_phase_runs "
                "(transaction_id, phase, attempt, status, idempotent, started_at) "
                "VALUES (?, ?, ?, 'running', ?, ?)",
                (transaction_id, str(phase)[:64], attempt, int(idempotent), utc_now()),
            )
            row = connection.execute(
                "SELECT * FROM adapter_phase_runs WHERE transaction_id=? AND phase=? AND attempt=?",
                (transaction_id, phase, attempt),
            ).fetchone()
        return self._phase_row(row)

    def finish_phase(
        self,
        transaction_id: str,
        phase: str,
        attempt: int,
        *,
        status: str,
        evidence: dict[str, Any] | None = None,
        external_ref: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        with self.connect(immediate=True) as connection:
            updated = connection.execute(
                "UPDATE adapter_phase_runs SET status=?, completed_at=?, evidence_json=?, "
                "external_ref=?, error=? WHERE transaction_id=? AND phase=? AND attempt=?",
                (
                    str(status)[:32],
                    utc_now(),
                    canonical_json(sanitize_evidence(evidence or {})),
                    sanitize_message(external_ref) or None,
                    sanitize_message(error) or None,
                    transaction_id,
                    phase,
                    attempt,
                ),
            )
            if updated.rowcount != 1:
                raise FleetStoreError("Unknown adapter phase attempt")
            row = connection.execute(
                "SELECT * FROM adapter_phase_runs WHERE transaction_id=? AND phase=? AND attempt=?",
                (transaction_id, phase, attempt),
            ).fetchone()
        return self._phase_row(row)

    def phase_runs(self, transaction_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM adapter_phase_runs WHERE transaction_id=? "
                "ORDER BY started_at, attempt",
                (transaction_id,),
            ).fetchall()
        return [self._phase_row(row) for row in rows]

    @staticmethod
    def _phase_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "transaction_id": row["transaction_id"],
            "phase": row["phase"],
            "attempt": row["attempt"],
            "status": row["status"],
            "idempotent": bool(row["idempotent"]),
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "evidence": json.loads(row["evidence_json"] or "{}"),
            "external_ref": row["external_ref"],
            "error": row["error"],
        }

    def qualify_adapter(
        self,
        *,
        adapter: str,
        host_id: str,
        component: str,
        version: str,
        rollback_rehearsed: bool,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        status = "qualified" if rollback_rehearsed else "blocked"
        qualified_at = utc_now() if rollback_rehearsed else None
        with self.connect(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO adapter_qualifications (
                  adapter, host_id, component, version, status,
                  rollback_rehearsed, evidence_json, qualified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(adapter, host_id, component) DO UPDATE SET
                  version=excluded.version,
                  status=excluded.status,
                  rollback_rehearsed=excluded.rollback_rehearsed,
                  evidence_json=excluded.evidence_json,
                  qualified_at=excluded.qualified_at
                """,
                (
                    adapter,
                    host_id,
                    component,
                    version,
                    status,
                    int(rollback_rehearsed),
                    canonical_json(sanitize_evidence(evidence)),
                    qualified_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM adapter_qualifications WHERE adapter=? AND host_id=? AND component=?",
                (adapter, host_id, component),
            ).fetchone()
        value = dict(row)
        value["rollback_rehearsed"] = bool(value["rollback_rehearsed"])
        value["evidence"] = json.loads(value.pop("evidence_json") or "{}")
        return value

    def adapter_qualified(self, adapter: str, host_id: str, component: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status, rollback_rehearsed FROM adapter_qualifications "
                "WHERE adapter=? AND host_id=? AND component=?",
                (adapter, host_id, component),
            ).fetchone()
        return bool(row and row["status"] == "qualified" and row["rollback_rehearsed"])

    def enroll_agent(
        self,
        *,
        agent_id: str,
        host_id: str,
        public_key: str,
        fingerprint: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.connect(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO agent_enrollments (
                  agent_id, host_id, public_key, fingerprint, state,
                  enrolled_at, metadata_json
                ) VALUES (?, ?, ?, ?, 'enrolled', ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                  host_id=excluded.host_id,
                  public_key=excluded.public_key,
                  fingerprint=excluded.fingerprint,
                  state='enrolled',
                  metadata_json=excluded.metadata_json
                """,
                (
                    agent_id,
                    host_id,
                    public_key,
                    fingerprint,
                    utc_now(),
                    canonical_json(sanitize_evidence(metadata or {})),
                ),
            )
        return self.get_agent(agent_id) or {}

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_enrollments WHERE agent_id=?", (agent_id,)
            ).fetchone()
        if not row:
            return None
        value = dict(row)
        value["metadata"] = json.loads(value.pop("metadata_json") or "{}")
        return value

    def record_agent_heartbeat(
        self,
        *,
        agent_id: str,
        nonce: str,
        body_hash: str,
        status: dict[str, Any],
    ) -> dict[str, Any]:
        received = utc_now()
        with self.connect(immediate=True) as connection:
            try:
                connection.execute(
                    "INSERT INTO agent_heartbeats "
                    "(agent_id, nonce, received_at, body_hash, status_json) VALUES (?, ?, ?, ?, ?)",
                    (
                        agent_id,
                        nonce,
                        received,
                        body_hash,
                        canonical_json(sanitize_evidence(status)),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise FleetStoreError("Duplicate agent nonce") from exc
            connection.execute(
                "UPDATE agent_enrollments SET last_nonce=?, last_seen_at=?, state='online' WHERE agent_id=?",
                (nonce, received, agent_id),
            )
        return {"agent_id": agent_id, "received_at": received}

    def queue_agent_command(
        self,
        *,
        agent_id: str,
        job_id: str,
        transaction_id: str | None,
        action: str,
        component: str,
        manifest: dict[str, Any],
        expires_at: str,
        not_before: str | None = None,
    ) -> dict[str, Any]:
        safe_manifest = sanitize_plan(manifest)
        manifest_json = canonical_json(safe_manifest)
        command_id = f"agentcmd-{uuid.uuid4().hex}"
        with self.connect(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO agent_commands (
                  id, agent_id, job_id, transaction_id, action, component,
                  manifest_json, manifest_hash, state, queued_at, not_before, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    command_id,
                    agent_id,
                    job_id,
                    transaction_id,
                    action,
                    component,
                    manifest_json,
                    hashlib.sha256(manifest_json.encode()).hexdigest(),
                    utc_now(),
                    not_before,
                    expires_at,
                ),
            )
        return self.get_agent_command(command_id) or {}

    def get_agent_command(self, command_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_commands WHERE id=?", (command_id,)
            ).fetchone()
        return self._agent_command_row(row) if row else None

    def deliver_agent_commands(self, agent_id: str, *, limit: int = 1) -> list[dict[str, Any]]:
        self.expire_pending_agent_commands(agent_id=agent_id)
        now = utc_now()
        with self.connect(immediate=True) as connection:
            rows = connection.execute(
                "SELECT * FROM agent_commands WHERE agent_id=? AND state IN ('pending','delivered') "
                "AND (not_before IS NULL OR not_before<=?) AND expires_at>? "
                "ORDER BY queued_at LIMIT ?",
                (agent_id, now, now, max(1, min(limit, 20))),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE agent_commands SET state='delivered', delivered_at=COALESCE(delivered_at, ?) WHERE id=?",
                    (now, row["id"]),
                )
            refreshed = [
                connection.execute("SELECT * FROM agent_commands WHERE id=?", (row["id"],)).fetchone()
                for row in rows
            ]
        return [self._agent_command_row(row) for row in refreshed if row]

    def finish_agent_command(
        self,
        command_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        state = "complete" if status == "complete" else "failed"
        already_terminal = False
        with self.connect(immediate=True) as connection:
            row = connection.execute(
                "SELECT job_id, state FROM agent_commands WHERE id=?", (command_id,)
            ).fetchone()
            if not row:
                raise FleetStoreError("Unknown agent command")
            if row["state"] in {"complete", "failed"}:
                already_terminal = True
            elif row["state"] not in {"pending", "delivered"}:
                raise FleetStoreError(f"Agent command cannot complete from state {row['state']}")
            else:
                connection.execute(
                    "UPDATE agent_commands SET state=?, completed_at=?, result_json=?, error=? WHERE id=?",
                    (
                        state,
                        utc_now(),
                        canonical_json(sanitize_evidence(result or {})),
                        sanitize_message(error) or None,
                        command_id,
                    ),
                )
        if already_terminal:
            return self.get_agent_command(command_id) or {}
        updated_job = self.update_job(
            row["job_id"], state="complete" if state == "complete" else "failed",
            result=result or {}, error=error, completed=True,
        )
        self.release_locks(str(row["job_id"]))
        compatibility = updated_job.get("compatibility_json_path")
        if compatibility:
            source = Path(str(compatibility))
            destination = self.state_root / "queue" / "completed" / f"{row['job_id']}.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                key: value
                for key, value in updated_job.items()
                if key != "compatibility_json_path"
            }
            temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
            temporary.write_text(canonical_json(sanitize_evidence(payload)) + "\n", encoding="utf-8")
            os.chmod(temporary, 0o660)
            os.replace(temporary, destination)
            if source != destination:
                source.unlink(missing_ok=True)
            with self.connect(immediate=True) as connection:
                connection.execute(
                    "UPDATE jobs SET compatibility_json_path=? WHERE id=?",
                    (str(destination), row["job_id"]),
                )
        return self.get_agent_command(command_id) or {}

    def expire_pending_agent_commands(self, *, agent_id: str | None = None) -> int:
        """Expire never-delivered offline jobs and release their mutation locks."""

        now = utc_now()
        where = "state='pending' AND expires_at<=?"
        parameters: list[Any] = [now]
        if agent_id:
            where += " AND agent_id=?"
            parameters.append(agent_id)
        with self.connect(immediate=True) as connection:
            rows = connection.execute(
                f"SELECT id, job_id, transaction_id FROM agent_commands WHERE {where}",
                parameters,
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE agent_commands SET state='expired', completed_at=?, "
                    "error='approval_expired' WHERE id=?",
                    (now, row["id"]),
                )
                connection.execute(
                    "UPDATE jobs SET state='cancelled', completed_at=?, "
                    "error='approval_expired' WHERE id=?",
                    (now, row["job_id"]),
                )
                connection.execute(
                    "DELETE FROM mutation_locks WHERE owner_job_id=?", (row["job_id"],)
                )
                if row["transaction_id"]:
                    connection.execute(
                        "UPDATE transactions SET state='cancelled', updated_at=?, "
                        "error='approval_expired' WHERE id=? AND state='approved'",
                        (now, row["transaction_id"]),
                    )
        for row in rows:
            self.append_event(
                str(row["job_id"]),
                phase="cancelled",
                level="warning",
                progress=100,
                message="Offline agent job expired before delivery; no mutation was attempted.",
                evidence={"command_id": row["id"], "reason": "approval_expired"},
                transaction_id=row["transaction_id"],
            )
            job = self.get_job(str(row["job_id"]))
            if job and job.get("compatibility_json_path"):
                source = Path(str(job["compatibility_json_path"]))
                destination = self.state_root / "queue" / "completed" / f"{row['job_id']}.json"
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
                temporary.write_text(
                    canonical_json(
                        sanitize_evidence(
                            {key: value for key, value in job.items() if key != "compatibility_json_path"}
                        )
                    )
                    + "\n",
                    encoding="utf-8",
                )
                os.chmod(temporary, 0o660)
                os.replace(temporary, destination)
                if source != destination:
                    source.unlink(missing_ok=True)
                with self.connect(immediate=True) as connection:
                    connection.execute(
                        "UPDATE jobs SET compatibility_json_path=? WHERE id=?",
                        (str(destination), row["job_id"]),
                    )
        return len(rows)

    @staticmethod
    def _agent_command_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["manifest"] = json.loads(value.pop("manifest_json") or "{}")
        value["result"] = json.loads(value.pop("result_json") or "{}")
        return value

    def add_recovery_point(self, value: dict[str, Any]) -> dict[str, Any]:
        recovery_id = str(value.get("id") or f"recovery-{uuid.uuid4().hex}")
        with self.connect(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO recovery_points (
                  id, host_id, component, version, checksum, checkpoint_type,
                  artifact_ref, metadata_json, compatible, verified, accepted,
                  created_at, verified_at, last_working
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recovery_id,
                    str(value["host_id"]),
                    str(value["component"]),
                    str(value["version"]),
                    str(value["checksum"]),
                    str(value["checkpoint_type"]),
                    str(value["artifact_ref"]),
                    canonical_json(sanitize_evidence(value.get("metadata") or {})),
                    int(bool(value.get("compatible", True))),
                    int(bool(value.get("verified", False))),
                    int(bool(value.get("accepted", False))),
                    str(value.get("created_at") or utc_now()),
                    value.get("verified_at"),
                    int(bool(value.get("last_working", False))),
                ),
            )
        return next(item for item in self.list_recovery_points() if item["id"] == recovery_id)

    def list_recovery_points(
        self, host_id: str | None = None, component: str | None = None
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if host_id:
            where.append("host_id=?")
            params.append(host_id)
        if component:
            where.append("component=?")
            params.append(component)
        clause = " WHERE " + " AND ".join(where) if where else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM recovery_points{clause} ORDER BY created_at DESC", params
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            for key in ("compatible", "verified", "accepted", "last_working"):
                item[key] = bool(item[key])
            output.append(item)
        return output

    def accept_recovery_point(self, recovery_id: str) -> dict[str, Any]:
        with self.connect(immediate=True) as connection:
            result = connection.execute(
                "UPDATE recovery_points SET accepted=1, last_working=1, verified=1, "
                "verified_at=COALESCE(verified_at, ?) WHERE id=? AND compatible=1",
                (utc_now(), recovery_id),
            )
            if result.rowcount != 1:
                raise FleetStoreError("Recovery point is absent or incompatible")
        return next(
            item for item in self.list_recovery_points() if item["id"] == recovery_id
        )

    def start_benchmark(
        self, suite: str, contract_version: str, host_id: str, triggered_by: str
    ) -> str:
        run_id = f"bench-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        with self.connect(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO benchmark_runs (
                  id, suite, contract_version, host_id, status, started_at, triggered_by
                ) VALUES (?, ?, ?, ?, 'running', ?, ?)
                """,
                (run_id, suite, contract_version, host_id, utc_now(), sanitize_message(triggered_by)),
            )
        return run_id

    def reconcile_stale_benchmarks(self, *, older_than_hours: int = 3) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=max(1, older_than_hours))
        ).isoformat()
        now = utc_now()
        with self.connect(immediate=True) as connection:
            result = connection.execute(
                "UPDATE benchmark_runs SET status='failed', critical_failures=1, "
                "completed_at=?, summary_json=? WHERE status='running' AND started_at<?",
                (
                    now,
                    canonical_json(
                        {
                            "reconciled": True,
                            "reason": "benchmark_process_interrupted",
                        }
                    ),
                    cutoff,
                ),
            )
        return int(result.rowcount)

    def add_benchmark_result(self, run_id: str, result: dict[str, Any]) -> None:
        with self.connect(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO benchmark_results (
                  run_id, category, probe, status, critical, elapsed_ms,
                  evidence_json, artifact_ref, cleanup_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, category, probe) DO UPDATE SET
                  status=excluded.status, elapsed_ms=excluded.elapsed_ms,
                  evidence_json=excluded.evidence_json, artifact_ref=excluded.artifact_ref,
                  cleanup_status=excluded.cleanup_status
                """,
                (
                    run_id,
                    str(result["category"]),
                    str(result["probe"]),
                    str(result["status"]),
                    int(bool(result.get("critical"))),
                    int(result.get("elapsed_ms") or 0),
                    canonical_json(sanitize_evidence(result.get("evidence") or {})),
                    result.get("artifact_ref"),
                    str(result.get("cleanup_status") or "not_applicable"),
                ),
            )

    def finish_benchmark(self, run_id: str, *, summary: dict[str, Any]) -> dict[str, Any]:
        with self.connect(immediate=True) as connection:
            results = connection.execute(
                "SELECT status, critical FROM benchmark_results WHERE run_id=?", (run_id,)
            ).fetchall()
            total = len(results)
            passed = sum(1 for row in results if row["status"] == "passed")
            critical_failures = sum(
                1 for row in results if row["critical"] and row["status"] != "passed"
            )
            score = round((passed / total * 100), 2) if total else 0
            status = "passed" if total and passed == total else "failed"
            connection.execute(
                """
                UPDATE benchmark_runs SET status=?, score=?, critical_failures=?,
                  completed_at=?, summary_json=? WHERE id=?
                """,
                (
                    status,
                    score,
                    critical_failures,
                    utc_now(),
                    canonical_json(sanitize_evidence(summary)),
                    run_id,
                ),
            )
        return self.get_benchmark(run_id) or {}

    def get_benchmark(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            run = connection.execute("SELECT * FROM benchmark_runs WHERE id=?", (run_id,)).fetchone()
            if not run:
                return None
            rows = connection.execute(
                "SELECT * FROM benchmark_results WHERE run_id=? ORDER BY category, probe", (run_id,)
            ).fetchall()
        value = dict(run)
        value["summary"] = json.loads(value.pop("summary_json") or "{}")
        value["results"] = []
        for row in rows:
            item = dict(row)
            item["critical"] = bool(item["critical"])
            item["evidence"] = json.loads(item.pop("evidence_json") or "{}")
            value["results"].append(item)
        return value

    def list_benchmarks(self, limit: int = 80) -> list[dict[str, Any]]:
        with self.connect() as connection:
            ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM benchmark_runs ORDER BY started_at DESC LIMIT ?",
                    (max(1, min(limit, 500)),),
                ).fetchall()
            ]
        return [value for value in (self.get_benchmark(item) for item in ids) if value]

    def prune_benchmark_details(self, completed_before: str) -> int:
        """Remove expired probe detail while retaining run-level trend summaries."""

        with self.connect(immediate=True) as connection:
            result = connection.execute(
                "DELETE FROM benchmark_results WHERE run_id IN ("
                "SELECT id FROM benchmark_runs WHERE completed_at IS NOT NULL AND completed_at<?"
                ")",
                (completed_before,),
            )
        return int(result.rowcount)

    def prune_detailed_history(self, completed_before: str) -> dict[str, int]:
        """Prune expired detail while retaining compact transaction/job summaries."""

        terminal = tuple(sorted(TERMINAL_TRANSACTION_STATES))
        placeholders = ",".join("?" for _ in terminal)
        with self.connect(immediate=True) as connection:
            events = connection.execute(
                "DELETE FROM job_events WHERE job_id IN ("
                "SELECT id FROM jobs WHERE completed_at IS NOT NULL AND completed_at<?)",
                (completed_before,),
            ).rowcount
            phases = connection.execute(
                f"DELETE FROM adapter_phase_runs WHERE transaction_id IN ("
                f"SELECT id FROM transactions WHERE state IN ({placeholders}) AND updated_at<?)",
                (*terminal, completed_before),
            ).rowcount
            approvals = connection.execute(
                f"DELETE FROM approvals WHERE transaction_id IN ("
                f"SELECT id FROM transactions WHERE state IN ({placeholders}) AND updated_at<?)",
                (*terminal, completed_before),
            ).rowcount
            heartbeats = connection.execute(
                "DELETE FROM agent_heartbeats WHERE received_at<?", (completed_before,)
            ).rowcount
        return {
            "job_events": int(events),
            "adapter_phase_runs": int(phases),
            "approvals": int(approvals),
            "agent_heartbeats": int(heartbeats),
        }

    def record_acceptance_gate(
        self,
        *,
        name: str,
        status: str,
        verified_by: str,
        evidence: dict[str, Any],
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,79}", name):
            raise FleetStoreError("Invalid acceptance gate name")
        if status not in {"passed", "failed", "pending"}:
            raise FleetStoreError("Invalid acceptance gate status")
        with self.connect(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO acceptance_gates (
                  name, status, evidence_json, verified_by, verified_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                  status=excluded.status,
                  evidence_json=excluded.evidence_json,
                  verified_by=excluded.verified_by,
                  verified_at=excluded.verified_at,
                  expires_at=excluded.expires_at
                """,
                (
                    name,
                    status,
                    canonical_json(sanitize_evidence(evidence)),
                    sanitize_message(verified_by),
                    utc_now(),
                    expires_at,
                ),
            )
        return next(item for item in self.list_acceptance_gates() if item["name"] == name)

    def list_acceptance_gates(self) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM acceptance_gates ORDER BY name"
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["evidence"] = json.loads(item.pop("evidence_json") or "{}")
            expires = item.get("expires_at")
            item["effective_status"] = (
                "expired"
                if expires and datetime.fromisoformat(str(expires)) <= now
                else item["status"]
            )
            output.append(item)
        return output

    def backup(self, destination: Path) -> dict[str, Any]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        with self.connect() as source:
            target = sqlite3.connect(temporary)
            try:
                source.backup(target)
                target.execute("PRAGMA quick_check")
            finally:
                target.close()
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        return {"path": str(destination), "sha256": digest, "bytes": destination.stat().st_size}

    def quick_check(self) -> str:
        with self.connect() as connection:
            return str(connection.execute("PRAGMA quick_check").fetchone()[0])
