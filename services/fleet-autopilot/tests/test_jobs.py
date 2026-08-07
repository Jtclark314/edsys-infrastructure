from __future__ import annotations

from pathlib import Path

from edsys_fleet.config import FleetConfig
from edsys_fleet.jobs import list_jobs, queue_job


def config(tmp_path: Path) -> FleetConfig:
    return FleetConfig(raw={"schema_version": 1, "state_root": str(tmp_path), "hosts": [], "proxmox": {}}, path=tmp_path / "config.yml")


def test_queue_writes_private_atomic_job(tmp_path: Path):
    cfg = config(tmp_path)
    job = queue_job(cfg, "inspect", requested_by="test")
    assert job["status"] == "pending"
    assert list_jobs(cfg)[0]["id"] == job["id"]
