"""Pydantic models used by the EdSys Control API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ServiceEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    slug: str
    category: str | None = None
    host: str | None = None
    ip: Any | None = None
    port: Any | None = None
    url: str | None = None
    protocol: str | None = None
    runtime: str | None = None
    owner: str | None = None
    criticality: str | None = None
    status: str | None = None
    backup_required: Any | None = None
    dependencies: list[Any] = Field(default_factory=list)
    confidence: str | None = None
    verify_live: bool | None = None
    source: str | None = None
    notes: str | None = None
    container_name: str | None = None
    image: str | None = None
    published_ports: Any | None = None
    mounts: list[Any] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class DeviceEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    hostname: str
    slug: str
    aliases: list[str] = Field(default_factory=list)
    role: str | None = None
    category: str | None = None
    ip: Any | None = None
    mac: str | None = None
    os: str | None = None
    interfaces: list[Any] = Field(default_factory=list)
    primary_services: list[Any] = Field(default_factory=list)
    management_urls: list[Any] = Field(default_factory=list)
    reachable_ports: list[Any] = Field(default_factory=list)
    status: str | None = None
    confidence: str | None = None
    source: str | None = None
    notes: str | None = None
    tailscale_ip: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class CatalogData(BaseModel):
    network_metadata: dict[str, Any] = Field(default_factory=dict)
    service_metadata: dict[str, Any] = Field(default_factory=dict)
    devices: list[DeviceEntry] = Field(default_factory=list)
    services: list[ServiceEntry] = Field(default_factory=list)
    loaded_at: str


class HealthCheckResult(BaseModel):
    name: str
    slug: str
    status: str
    target: str | None = None
    check_type: str | None = None
    checked_at: str
    latency_ms: float | None = None
    http_status: int | None = None
    reason: str | None = None
    scope: str = "core"
    criticality: str | None = None
    host: str | None = None
    ip: Any | None = None
    port: Any | None = None
    url: str | None = None
