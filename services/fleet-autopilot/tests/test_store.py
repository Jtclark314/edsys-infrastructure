from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from edsys_fleet.store import FleetStore, FleetStoreError


def job() -> dict[str, object]:
    return {
        "id": "fleet-test-job",
        "action": "inspect",
        "target": "all",
        "component": "",
        "status": "pending",
        "requested_by": "test",
        "requested_at": "2026-08-06T00:00:00+00:00",
    }


def test_store_migrates_to_wal_and_private_database(tmp_path: Path):
    store = FleetStore(tmp_path)
    assert store.quick_check() == "ok"
    assert store.path.stat().st_mode & 0o777 == 0o660
    with store.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_event_journal_is_hash_chained_bounded_and_sanitized(tmp_path: Path):
    store = FleetStore(tmp_path)
    store.upsert_job(job())
    first = store.append_event(
        "fleet-test-job",
        phase="preflight",
        message="Authorization: Bearer abcdefghijklmnop",
        evidence={"password": "unsafe", "auth_status": "o_auth"},
    )
    second = store.append_event(
        "fleet-test-job", phase="verify", message="safe", evidence={"ok": True}
    )
    assert "abcdefghijklmnop" not in json.dumps(first)
    assert first["evidence"]["password"] == "[REDACTED]"
    assert first["evidence"]["auth_status"] == "o_auth"
    assert second["sequence"] == 2
    assert second["previous_hash"] == first["event_hash"]


def test_transaction_state_machine_and_typed_reboot_approval(tmp_path: Path):
    store = FleetStore(tmp_path)
    transaction = store.create_transaction(
        {
            "host_id": "9950x",
            "component": "linux-kernel",
            "action": "upgrade",
            "risk_class": "reboot",
            "candidate": "approved",
            "requested_by": "owner",
        }
    )
    with pytest.raises(FleetStoreError):
        store.transition_transaction(transaction["id"], "applying")
    store.transition_transaction(transaction["id"], "preflight")
    store.transition_transaction(transaction["id"], "awaiting_approval")
    phrase = f"APPROVE {transaction['plan_hash'][-8:]}"
    with pytest.raises(FleetStoreError):
        store.approve_transaction(
            transaction["id"],
            identity="owner",
            plan_hash=transaction["plan_hash"],
            typed_phrase="wrong",
        )
    approved = store.approve_transaction(
        transaction["id"],
        identity="owner",
        plan_hash=transaction["plan_hash"],
        typed_phrase=phrase,
    )
    assert approved["state"] == "approved"
    assert approved["approvals"][0]["identity"] == "owner"


def test_encrypted_backup_source_can_be_restored_by_sqlite_backup_api(tmp_path: Path):
    store = FleetStore(tmp_path / "live")
    store.upsert_job(job())
    result = store.backup(tmp_path / "copy.sqlite")
    assert result["bytes"] > 0
    copy = FleetStore(tmp_path / "restored")
    assert copy.quick_check() == "ok"


def test_sqlite_claim_parameters_and_phase_reconciliation_evidence(tmp_path: Path):
    store = FleetStore(tmp_path)
    value = {**job(), "parameters": {"candidate": "24.19.0"}}
    store.upsert_job(value)
    claimed = store.claim_next_job()
    assert claimed and claimed["status"] == "running"
    assert claimed["parameters"] == {"candidate": "24.19.0"}

    transaction = store.create_transaction(
        {"host_id": "nimo", "component": "node-toolchain", "action": "upgrade", "requested_by": "owner"}
    )
    phase = store.start_phase(transaction["id"], "preflight", idempotent=True)
    completed = store.finish_phase(
        transaction["id"], "preflight", phase["attempt"], status="passed", evidence={"ok": True}
    )
    assert completed["evidence"] == {"ok": True}
    assert store.phase_runs(transaction["id"])[0]["idempotent"] is True


def test_agent_nonce_replay_and_expiring_outbound_command(tmp_path: Path):
    store = FleetStore(tmp_path)
    enrolled = store.enroll_agent(
        agent_id="dell-1", host_id="work-laptop", public_key="aa" * 32,
        fingerprint="sha256:test", metadata={"platform": "windows"},
    )
    assert enrolled["state"] == "enrolled"
    store.record_agent_heartbeat(
        agent_id="dell-1", nonce="nonce-1", body_hash="bb" * 32, status={"state": "ready"}
    )
    with pytest.raises(FleetStoreError):
        store.record_agent_heartbeat(
            agent_id="dell-1", nonce="nonce-1", body_hash="bb" * 32, status={"state": "ready"}
        )
    agent_job = {**job(), "id": "fleet-agent-job", "target": "work-laptop", "action": "upgrade"}
    store.upsert_job(agent_job)
    command = store.queue_agent_command(
        agent_id="dell-1", job_id="fleet-agent-job", transaction_id=None,
        action="upgrade", component="node-toolchain", manifest={"plan_hash": "cc" * 32},
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    )
    delivered = store.deliver_agent_commands("dell-1")
    assert [item["id"] for item in delivered] == [command["id"]]
    finished = store.finish_agent_command(command["id"], status="complete", result={"passed": True})
    assert finished["state"] == "complete"


def test_acceptance_gate_is_sanitized_and_reports_expiry(tmp_path: Path):
    store = FleetStore(tmp_path)
    value = store.record_acceptance_gate(
        name="break_glass_drill",
        status="passed",
        verified_by="owner",
        evidence={"token": "must-not-persist", "control": "single-use"},
        expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    )
    assert value["effective_status"] == "passed"
    assert value["evidence"]["token"] == "[REDACTED]"


def test_executable_plan_is_preserved_beyond_event_limit_and_rejects_secrets(tmp_path: Path):
    store = FleetStore(tmp_path)
    long_plan = {
        "adapter_manifest": {
            "argv": ["a" * 8000, "b" * 8000, "c" * 8000],
            "candidate": {"sha256": "d" * 64},
        }
    }
    transaction = store.create_transaction(
        {
            "host_id": "9950x",
            "component": "test",
            "requested_by": "owner",
            "parameters": long_plan,
        }
    )
    assert transaction["summary"]["parameters"] == long_plan
    with pytest.raises(FleetStoreError):
        store.create_transaction(
            {
                "host_id": "9950x",
                "component": "test",
                "requested_by": "owner",
                "parameters": {"api_key": "must-not-enter-a-plan"},
            }
        )


def test_mutation_lock_can_be_renewed_only_by_same_job(tmp_path: Path):
    store = FleetStore(tmp_path)
    store.upsert_job(job())
    other = {**job(), "id": "fleet-other-job"}
    store.upsert_job(other)
    store.acquire_lock("host:9950x", "fleet-test-job")
    store.acquire_lock("host:9950x", "fleet-test-job")
    with pytest.raises(FleetStoreError):
        store.acquire_lock("host:9950x", "fleet-other-job")


def test_offline_agent_approval_expiry_cancels_job_and_releases_lock(tmp_path: Path):
    store = FleetStore(tmp_path)
    store.enroll_agent(
        agent_id="dell-expiry",
        host_id="work-laptop",
        public_key="aa" * 32,
        fingerprint="sha256:expiry",
    )
    agent_job = {
        **job(),
        "id": "fleet-expired-agent-job",
        "target": "work-laptop",
        "action": "upgrade",
    }
    store.upsert_job(agent_job)
    store.acquire_lock("host:work-laptop", agent_job["id"])
    store.queue_agent_command(
        agent_id="dell-expiry",
        job_id=agent_job["id"],
        transaction_id=None,
        action="upgrade",
        component="node-toolchain",
        manifest={"kind": "inventory"},
        expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    )

    assert store.expire_pending_agent_commands() == 1
    assert store.get_job(agent_job["id"])["status"] == "cancelled"
    with store.connect() as connection:
        assert connection.execute("SELECT count(*) FROM mutation_locks").fetchone()[0] == 0


def test_detailed_history_prunes_only_expired_detail(tmp_path: Path):
    store = FleetStore(tmp_path)
    value = job()
    value.update({"status": "complete", "completed_at": "2020-01-01T00:00:00+00:00"})
    store.upsert_job(value)
    store.append_event(value["id"], phase="complete", message="old detail")

    pruned = store.prune_detailed_history("2021-01-01T00:00:00+00:00")

    assert pruned["job_events"] == 1
    assert store.get_job(value["id"])["status"] == "complete"


def test_stale_running_benchmark_is_failed_closed(tmp_path: Path):
    store = FleetStore(tmp_path)
    run_id = store.start_benchmark("deterministic", "2.0.0", "9950x", "test")
    with store.connect(immediate=True) as connection:
        connection.execute(
            "UPDATE benchmark_runs SET started_at='2020-01-01T00:00:00+00:00' WHERE id=?",
            (run_id,),
        )

    assert store.reconcile_stale_benchmarks() == 1
    value = store.get_benchmark(run_id)
    assert value and value["status"] == "failed"
    assert value["critical_failures"] == 1
