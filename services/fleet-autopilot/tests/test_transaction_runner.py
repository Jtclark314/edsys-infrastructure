from __future__ import annotations

from pathlib import Path

from edsys_fleet.config import FleetConfig
from edsys_fleet.io import write_json_atomic
from edsys_fleet.jobs import FleetJobRunner, queue_job
from edsys_fleet.store import FleetStore


def config(tmp_path: Path) -> FleetConfig:
    return FleetConfig(
        raw={
            "schema_version": 2,
            "policy_version": "test",
            "state_root": str(tmp_path),
            "private_artifact_root": str(tmp_path / "private"),
            "compatibility_json_queue": True,
            "hosts": [{"id": "test-host", "transport": "local", "platform": "linux"}],
            "components": {
                "test-component": {
                    "hosts": ["test-host"],
                    "adapter": "test-adapter",
                    "risk_class": "ordinary",
                    "desired": "2.0.0",
                    "absence": "missing",
                    "supports": {},
                    "observation_required": False,
                }
            },
            "proxmox": {},
        },
        path=tmp_path / "policy.yml",
    )


def manifest(*, fail_apply: bool = False) -> dict:
    phases = {}
    for phase in (
        "discover", "resolve_candidate", "preflight", "checkpoint", "apply",
        "restart_or_reboot", "verify", "accept", "rollback", "cleanup",
    ):
        exit_code = 7 if fail_apply and phase == "apply" else 0
        phases[phase] = {
            "argv": ["python3", "-c", f"print('{phase}-ok'); raise SystemExit({exit_code})"],
            "stdout_contains": f"{phase}-ok",
        }
    phases["checkpoint"].update(
        {
            "artifact_ref": "private://test/recovery",
            "artifact_sha256": "c" * 64,
            "checkpoint_type": "test",
        }
    )
    phases["verify_rollback"] = {
        "argv": ["python3", "-c", "print('verify-rollback-ok')"],
        "stdout_contains": "verify-rollback-ok",
    }
    return {
        "adapter": "test-adapter",
        "candidate": {"version": "2.0.0", "source": "https://example.test/release", "sha256": "a" * 64},
        "rollback": {"version": "1.0.0", "source": "private://test/rollback", "sha256": "b" * 64},
        "phases": phases,
    }


def approved_transaction(
    cfg: FleetConfig,
    *,
    parameters: dict,
    action: str = "upgrade",
    recovery_point_id: str | None = None,
    current_version: str = "1.0.0",
    candidate: str = "2.0.0",
) -> tuple[FleetStore, dict]:
    store = FleetStore(cfg.state_root)
    write_json_atomic(
        cfg.state_root / "snapshot.json",
        {"hosts": [{"id": "test-host", "status": "ok", "reachable": True, "versions": {}}]},
    )
    transaction = store.create_transaction(
        {
            "host_id": "test-host",
            "component": "test-component",
            "action": action,
            "risk_class": "ordinary",
            "current_version": current_version,
            "candidate": candidate,
            "requested_by": "owner",
            "recovery_point_id": recovery_point_id,
            "parameters": parameters,
        }
    )
    store.transition_transaction(transaction["id"], "preflight")
    store.transition_transaction(transaction["id"], "awaiting_approval")
    transaction = store.approve_transaction(
        transaction["id"], identity="owner", plan_hash=transaction["plan_hash"], typed_phrase=None
    )
    return store, transaction


def test_qualification_rehearses_rollback_reapplies_and_accepts(tmp_path: Path):
    cfg = config(tmp_path)
    store, transaction = approved_transaction(
        cfg, parameters={"qualification": True, "adapter_manifest": manifest()}
    )
    queue_job(
        cfg, "upgrade", "test-host", "test-component",
        parameters={"transaction_id": transaction["id"]},
        requested_by="owner", transaction_id=transaction["id"], idempotency_key=transaction["plan_hash"],
    )
    completed = FleetJobRunner(cfg).process_one()
    assert completed and completed["status"] == "complete"
    assert store.get_transaction(transaction["id"])["state"] == "accepted"
    assert store.adapter_qualified("test-adapter", "test-host", "test-component") is True
    phases = [item["phase"] for item in store.phase_runs(transaction["id"])]
    assert "qualification_rollback" in phases
    assert "qualification_reapply" in phases
    recovery = store.list_recovery_points("test-host", "test-component")[0]
    assert recovery["verified"] is True
    assert recovery["accepted"] is True
    assert recovery["last_working"] is True


def test_failed_apply_automatically_rolls_back_component(tmp_path: Path):
    cfg = config(tmp_path)
    store, transaction = approved_transaction(
        cfg, parameters={"adapter_manifest": manifest(fail_apply=True)}
    )
    store.qualify_adapter(
        adapter="test-adapter", host_id="test-host", component="test-component",
        version="1.0.0", rollback_rehearsed=True, evidence={"prior": True},
    )
    queue_job(
        cfg, "upgrade", "test-host", "test-component",
        parameters={"transaction_id": transaction["id"]},
        requested_by="owner", transaction_id=transaction["id"], idempotency_key=transaction["plan_hash"],
    )
    completed = FleetJobRunner(cfg).process_one()
    assert completed and completed["status"] == "failed"
    assert store.get_transaction(transaction["id"])["state"] == "rolled_back"
    assert any(item["phase"] == "automatic_rollback" and item["status"] == "passed" for item in store.phase_runs(transaction["id"]))


def test_cancellation_after_apply_waits_for_boundary_and_rolls_back(tmp_path: Path):
    cfg = config(tmp_path)
    value = manifest()
    value["phases"]["apply"] = {
        "argv": [
            "python3",
            "-c",
            (
                "import sqlite3;"
                f"c=sqlite3.connect({str(tmp_path / 'fleet-control.sqlite')!r});"
                "c.execute(\"update transactions set cancellation_requested=1 where state='applying'\");"
                "c.commit();print('apply-ok')"
            ),
        ],
        "stdout_contains": "apply-ok",
    }
    store, transaction = approved_transaction(
        cfg, parameters={"adapter_manifest": value}
    )
    store.qualify_adapter(
        adapter="test-adapter",
        host_id="test-host",
        component="test-component",
        version="1.0.0",
        rollback_rehearsed=True,
        evidence={"prior": True},
    )
    queue_job(
        cfg,
        "upgrade",
        "test-host",
        "test-component",
        parameters={"transaction_id": transaction["id"]},
        requested_by="owner",
        transaction_id=transaction["id"],
        idempotency_key=transaction["plan_hash"],
    )

    completed = FleetJobRunner(cfg).process_one()

    assert completed and completed["status"] == "cancelled"
    assert store.get_transaction(transaction["id"])["state"] == "rolled_back"
    assert any(
        item["phase"] == "automatic_rollback" and item["status"] == "passed"
        for item in store.phase_runs(transaction["id"])
    )


def test_failed_requested_rollback_restores_fresh_safety_checkpoint(tmp_path: Path):
    cfg = config(tmp_path)
    store = FleetStore(cfg.state_root)
    selected = store.add_recovery_point(
        {
            "id": "recovery-selected",
            "host_id": "test-host",
            "component": "test-component",
            "version": "1.0.0",
            "checksum": "d" * 64,
            "checkpoint_type": "test",
            "artifact_ref": "private://test/selected",
            "compatible": True,
            "verified": True,
            "accepted": True,
        }
    )
    state = tmp_path / "component-version"
    state.write_text("2.0.0", encoding="utf-8")
    value = manifest()
    value["phases"].update(
        {
            "rollback_selected": {
                "argv": [
                    "python3",
                    "-c",
                    f"from pathlib import Path; Path({str(state)!r}).write_text('1.0.0'); print('selected-ok')",
                ],
                "stdout_contains": "selected-ok",
            },
            "verify_rollback": {
                "argv": ["python3", "-c", "print('selected-invalid'); raise SystemExit(9)"],
                "stdout_contains": "selected-ok",
            },
            "restore_checkpoint": {
                "argv": [
                    "python3",
                    "-c",
                    f"from pathlib import Path; Path({str(state)!r}).write_text('2.0.0'); print('safety-ok')",
                ],
                "stdout_contains": "safety-ok",
            },
            "verify": {
                "argv": [
                    "python3",
                    "-c",
                    f"from pathlib import Path; assert Path({str(state)!r}).read_text() == '2.0.0'; print('verify-ok')",
                ],
                "stdout_contains": "verify-ok",
            },
        }
    )
    store, transaction = approved_transaction(
        cfg,
        parameters={"adapter_manifest": value},
        action="rollback",
        recovery_point_id=selected["id"],
        current_version="2.0.0",
        candidate="1.0.0",
    )
    store.qualify_adapter(
        adapter="test-adapter",
        host_id="test-host",
        component="test-component",
        version="2.0.0",
        rollback_rehearsed=True,
        evidence={"prior": True},
    )
    queue_job(
        cfg,
        "rollback",
        "test-host",
        "test-component",
        parameters={"transaction_id": transaction["id"]},
        requested_by="owner",
        transaction_id=transaction["id"],
        idempotency_key=transaction["plan_hash"],
    )

    completed = FleetJobRunner(cfg).process_one()

    assert completed and completed["status"] == "failed"
    assert state.read_text(encoding="utf-8") == "2.0.0"
    assert store.get_transaction(transaction["id"])["state"] == "rolled_back"
    phases = store.phase_runs(transaction["id"])
    assert any(item["phase"] == "rolling_back_selected" for item in phases)
    assert any(item["phase"] == "automatic_rollback" and item["status"] == "passed" for item in phases)
    assert any(item["phase"] == "automatic_rollback_verify" and item["status"] == "passed" for item in phases)
    safety = next(
        item
        for item in store.list_recovery_points("test-host", "test-component")
        if item["id"] != selected["id"]
    )
    assert safety["version"] == "2.0.0"
    assert safety["accepted"] is True
    assert safety["last_working"] is True


def test_failed_qualification_rollback_reapplies_verified_candidate(tmp_path: Path):
    cfg = config(tmp_path)
    state = tmp_path / "qualification-version"
    state.write_text("1.0.0", encoding="utf-8")
    value = manifest()
    value["phases"].update(
        {
            "apply": {
                "argv": [
                    "python3",
                    "-c",
                    f"from pathlib import Path; Path({str(state)!r}).write_text('2.0.0'); print('apply-ok')",
                ],
                "stdout_contains": "apply-ok",
            },
            "rollback": {
                "argv": [
                    "python3",
                    "-c",
                    f"from pathlib import Path; Path({str(state)!r}).write_text('1.0.0'); print('rollback-ok')",
                ],
                "stdout_contains": "rollback-ok",
            },
            "verify": {
                "argv": [
                    "python3",
                    "-c",
                    f"from pathlib import Path; assert Path({str(state)!r}).read_text() == '2.0.0'; print('verify-ok')",
                ],
                "stdout_contains": "verify-ok",
            },
            "verify_rollback": {
                "argv": ["python3", "-c", "print('rollback-invalid'); raise SystemExit(8)"],
                "stdout_contains": "verify-rollback-ok",
            },
        }
    )
    store, transaction = approved_transaction(
        cfg,
        parameters={"qualification": True, "adapter_manifest": value},
    )
    queue_job(
        cfg,
        "upgrade",
        "test-host",
        "test-component",
        parameters={"transaction_id": transaction["id"]},
        requested_by="owner",
        transaction_id=transaction["id"],
        idempotency_key=transaction["plan_hash"],
    )

    completed = FleetJobRunner(cfg).process_one()

    assert completed and completed["status"] == "failed"
    assert state.read_text(encoding="utf-8") == "2.0.0"
    assert store.get_transaction(transaction["id"])["state"] == "rolled_back"
    phases = store.phase_runs(transaction["id"])
    assert any(item["phase"] == "verify_rollback" and item["status"] == "failed" for item in phases)
    assert any(item["phase"] == "automatic_rollback" and item["status"] == "passed" for item in phases)
    assert any(item["phase"] == "automatic_rollback_verify" and item["status"] == "passed" for item in phases)
    assert store.adapter_qualified("test-adapter", "test-host", "test-component") is False
