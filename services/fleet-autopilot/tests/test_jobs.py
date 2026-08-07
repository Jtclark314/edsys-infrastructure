from __future__ import annotations

from pathlib import Path

from edsys_fleet.config import FleetConfig
from edsys_fleet.io import write_json_atomic
from edsys_fleet.jobs import FleetJobRunner, list_jobs, queue_job
from edsys_fleet.store import FleetStore


def config(tmp_path: Path) -> FleetConfig:
    return FleetConfig(raw={"schema_version": 1, "state_root": str(tmp_path), "hosts": [], "proxmox": {}}, path=tmp_path / "config.yml")


def test_queue_writes_private_atomic_job(tmp_path: Path):
    cfg = config(tmp_path)
    job = queue_job(cfg, "inspect", requested_by="test")
    assert job["status"] == "pending"
    assert list_jobs(cfg)[0]["id"] == job["id"]


def test_restart_removes_stale_pending_mirror_for_durable_terminal_job(
    tmp_path: Path,
) -> None:
    cfg = config(tmp_path)
    job = {
        "id": "fleet-compatibility-control",
        "action": "inspect",
        "target": "all",
        "component": "",
        "parameters": {},
        "requested_by": "test",
        "requested_at": "2026-08-07T00:00:00+00:00",
        "status": "complete",
        "completed_at": "2026-08-07T00:01:00+00:00",
        "result": {"status": "verified"},
    }
    pending = tmp_path / "queue" / "pending" / f"{job['id']}.json"
    completed = tmp_path / "queue" / "completed" / f"{job['id']}.json"
    write_json_atomic(pending, {**job, "status": "pending"}, mode=0o660)
    write_json_atomic(completed, job, mode=0o660)
    store = FleetStore(tmp_path)
    store.upsert_job(job, compatibility_path=str(completed))

    FleetJobRunner(cfg)

    assert not pending.exists()
    assert completed.exists()
    durable = store.get_job(job["id"])
    assert durable and durable["status"] == "complete"
    assert durable["compatibility_json_path"] == str(completed)
