from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG = Path(__file__).with_name("fleet-policy.yml")


@dataclass(frozen=True)
class FleetConfig:
    raw: dict[str, Any]
    path: Path

    @property
    def state_root(self) -> Path:
        override = os.getenv("EDSYS_FLEET_STATE_ROOT")
        return Path(override or self.raw["state_root"]).expanduser()

    @property
    def approved(self) -> dict[str, str]:
        return {str(k): str(v) for k, v in (self.raw.get("approved") or {}).items()}

    @property
    def hosts(self) -> list[dict[str, Any]]:
        return list(self.raw.get("hosts") or [])

    @property
    def proxmox(self) -> dict[str, Any]:
        return dict(self.raw.get("proxmox") or {})

    @property
    def timeout(self) -> int:
        return int(self.raw.get("command_timeout_seconds", 24))


def load_config(path: str | Path | None = None) -> FleetConfig:
    selected = Path(path or os.getenv("EDSYS_FLEET_CONFIG") or DEFAULT_CONFIG)
    data = yaml.safe_load(selected.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError(f"Invalid fleet policy: {selected}")
    return FleetConfig(raw=data, path=selected)
