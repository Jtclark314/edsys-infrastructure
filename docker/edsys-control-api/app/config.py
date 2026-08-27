"""Runtime configuration for the EdSys Control API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    network_map: Path
    service_catalog: Path
    grounding_index: Path = Path("/rag/edsys-grounding.sqlite")
    grounding_bearer_token: str | None = None
    grounding_freshness_seconds: int = 900
    grounding_top_k: int = 6
    health_timeout_seconds: float = 2.0
    enable_live_checks: bool = True
    cache_seconds: int = 30
    api_title: str = "EdSys Control API"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        network_map=Path(os.getenv("EDSYS_NETWORK_MAP", "/data/network-map.yml")),
        service_catalog=Path(
            os.getenv("EDSYS_SERVICE_CATALOG", "/data/service-catalog.yml")
        ),
        grounding_index=Path(
            os.getenv("EDSYS_GROUNDING_INDEX", "/rag/edsys-grounding.sqlite")
        ),
        grounding_bearer_token=os.getenv("EDSYS_GROUNDING_BEARER_TOKEN"),
        grounding_freshness_seconds=int(
            os.getenv("EDSYS_GROUNDING_FRESHNESS_SECONDS", "900")
        ),
        grounding_top_k=int(os.getenv("EDSYS_GROUNDING_TOP_K", "6")),
        health_timeout_seconds=float(os.getenv("EDSYS_HEALTH_TIMEOUT_SECONDS", "2")),
        enable_live_checks=_env_bool("EDSYS_ENABLE_LIVE_CHECKS", True),
        cache_seconds=int(os.getenv("EDSYS_CACHE_SECONDS", "30")),
        api_title=os.getenv("EDSYS_API_TITLE", "EdSys Control API"),
    )
