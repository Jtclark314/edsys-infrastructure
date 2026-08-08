from __future__ import annotations

import json
from pathlib import Path

from edsys_fleet.config import FleetConfig
from edsys_fleet.fleet_control_mcp import status_value
from edsys_fleet.store import FleetStore


def test_status_value_reads_durable_state_without_approval_surface(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "snapshot.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-08-07T12:00:00Z",
                "fresh": True,
                "summary": {"hosts_online": 3},
                "hosts": [
                    {"id": "9950x", "status": "ok", "reachable": True, "drift": []}
                ],
            }
        ),
        encoding="utf-8",
    )
    config = FleetConfig(
        raw={
            "schema_version": 2,
            "policy_version": "test",
            "state_root": str(state),
            "components": {},
        },
        path=tmp_path / "policy.yml",
    )
    store = FleetStore(state)

    value = status_value(config, store)

    assert value["database"]["quick_check"] == "ok"
    assert value["snapshot"]["hosts"][0]["id"] == "9950x"
    assert "approve" not in json.dumps(value).lower()
