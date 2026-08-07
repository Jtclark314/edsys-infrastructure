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
    def policy_version(self) -> str:
        return str(self.raw.get("policy_version") or self.raw.get("schema_version") or "1")

    @property
    def components(self) -> dict[str, dict[str, Any]]:
        return {
            str(name): dict(value)
            for name, value in (self.raw.get("components") or {}).items()
            if isinstance(value, dict)
        }

    def component(self, name: str) -> dict[str, Any]:
        try:
            return self.components[name]
        except KeyError as exc:
            raise ValueError(f"Unknown Fleet component: {name}") from exc

    @property
    def private_artifact_root(self) -> Path:
        override = os.getenv("EDSYS_FLEET_PRIVATE_ARTIFACT_ROOT")
        return Path(override or self.raw.get("private_artifact_root") or self.state_root / "artifacts").expanduser()

    @property
    def compatibility_json_queue(self) -> bool:
        return bool(self.raw.get("compatibility_json_queue", True))

    @property
    def event_retention_days(self) -> int:
        return max(1, int(self.raw.get("event_retention_days", 730)))

    @property
    def raw_benchmark_retention_days(self) -> int:
        return max(1, int(self.raw.get("raw_benchmark_retention_days", 30)))

    @property
    def benchmark_detail_retention_days(self) -> int:
        return max(1, int(self.raw.get("benchmark_detail_retention_days", 730)))

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
    if not isinstance(data, dict) or data.get("schema_version") not in {1, 2}:
        raise ValueError(f"Invalid fleet policy: {selected}")
    return FleetConfig(raw=data, path=selected)
