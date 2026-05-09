"""Safe read-only service health checks."""

from __future__ import annotations

import asyncio
import socket
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from .catalog_loader import CatalogStore, slugify
from .config import Settings
from .models import HealthCheckResult, ServiceEntry


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_int_port(value: Any) -> int | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    if 1 <= port <= 65535:
        return port
    return None


def _valid_single_ip(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or "-" in text or "/" in text or "," in text:
        return None
    return text


def _scope_for_service(service: ServiceEntry) -> str:
    category = str(service.category or "").lower()
    status = str(service.status or "").lower()
    if "external" in category or "external" in status or "project" in status:
        return "external-project-reference"
    return "core"


class HealthChecker:
    def __init__(self, settings: Settings, catalog: CatalogStore):
        self.settings = settings
        self.catalog = catalog
        self._all_cache: tuple[float, dict[str, Any]] | None = None
        self._service_cache: dict[str, tuple[float, HealthCheckResult]] = {}

    def _cache_valid(self, expires_at: float) -> bool:
        return time.monotonic() < expires_at

    def _target_for_service(self, service: ServiceEntry) -> tuple[str | None, str | None, str | None, int | None]:
        url = str(service.url or "").strip()
        if url.startswith(("http://", "https://")):
            return "http", url, None, None
        if url.startswith("tcp://"):
            parsed = urlparse(url)
            if parsed.hostname and parsed.port:
                return "tcp", url, parsed.hostname, parsed.port
            return "skip", None, None, None
        ip = _valid_single_ip(service.ip)
        port = _as_int_port(service.port)
        if ip and port:
            return "tcp", f"{ip}:{port}", ip, port
        return "skip", None, None, None

    async def _check_http(self, service: ServiceEntry, url: str) -> HealthCheckResult:
        started = time.perf_counter()
        checked_at = _now_iso()
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.health_timeout_seconds,
                follow_redirects=False,
                headers={"User-Agent": "EdSys-Control-API/0.1"},
            ) as client:
                response = await client.head(url)
                if response.status_code in {405, 501}:
                    response = await client.get(url)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            if 200 <= response.status_code <= 399:
                status = "up"
                reason = "http_success"
            elif response.status_code in {401, 403}:
                status = "reachable_auth_required"
                reason = "http_auth_required"
            else:
                status = "down"
                reason = f"http_status_{response.status_code}"
            return HealthCheckResult(
                name=service.name,
                slug=service.slug,
                status=status,
                target=url,
                check_type="http",
                checked_at=checked_at,
                latency_ms=elapsed_ms,
                http_status=response.status_code,
                reason=reason,
                scope=_scope_for_service(service),
                criticality=service.criticality,
                host=service.host,
                ip=service.ip,
                port=service.port,
                url=service.url,
            )
        except Exception as exc:  # noqa: BLE001 - health probes should classify all failures.
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            return HealthCheckResult(
                name=service.name,
                slug=service.slug,
                status="down",
                target=url,
                check_type="http",
                checked_at=checked_at,
                latency_ms=elapsed_ms,
                reason=f"{type(exc).__name__}: {exc}",
                scope=_scope_for_service(service),
                criticality=service.criticality,
                host=service.host,
                ip=service.ip,
                port=service.port,
                url=service.url,
            )

    async def _check_tcp(self, service: ServiceEntry, target: str, host: str, port: int) -> HealthCheckResult:
        started = time.perf_counter()
        checked_at = _now_iso()

        def connect() -> None:
            with socket.create_connection((host, port), timeout=self.settings.health_timeout_seconds):
                return None

        try:
            await asyncio.to_thread(connect)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            return HealthCheckResult(
                name=service.name,
                slug=service.slug,
                status="up",
                target=target,
                check_type="tcp",
                checked_at=checked_at,
                latency_ms=elapsed_ms,
                reason="tcp_connect_success",
                scope=_scope_for_service(service),
                criticality=service.criticality,
                host=service.host,
                ip=service.ip,
                port=service.port,
                url=service.url,
            )
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            return HealthCheckResult(
                name=service.name,
                slug=service.slug,
                status="down",
                target=target,
                check_type="tcp",
                checked_at=checked_at,
                latency_ms=elapsed_ms,
                reason=f"{type(exc).__name__}: {exc}",
                scope=_scope_for_service(service),
                criticality=service.criticality,
                host=service.host,
                ip=service.ip,
                port=service.port,
                url=service.url,
            )

    async def check_service(self, service: ServiceEntry, force: bool = False) -> HealthCheckResult:
        cached = self._service_cache.get(service.slug)
        if not force and cached and self._cache_valid(cached[0]):
            return cached[1]

        checked_at = _now_iso()
        if not self.settings.enable_live_checks:
            result = HealthCheckResult(
                name=service.name,
                slug=service.slug,
                status="skipped",
                checked_at=checked_at,
                reason="live_checks_disabled",
                scope=_scope_for_service(service),
                criticality=service.criticality,
                host=service.host,
                ip=service.ip,
                port=service.port,
                url=service.url,
            )
        else:
            check_type, target, host, port = self._target_for_service(service)
            if check_type == "http" and target:
                result = await self._check_http(service, target)
            elif check_type == "tcp" and target and host and port:
                result = await self._check_tcp(service, target, host, port)
            else:
                result = HealthCheckResult(
                    name=service.name,
                    slug=service.slug,
                    status="skipped",
                    checked_at=checked_at,
                    reason="no_check_target",
                    scope=_scope_for_service(service),
                    criticality=service.criticality,
                    host=service.host,
                    ip=service.ip,
                    port=service.port,
                    url=service.url,
                )

        self._service_cache[service.slug] = (time.monotonic() + max(1, self.settings.cache_seconds), result)
        return result

    async def check_all(self, force: bool = False) -> dict[str, Any]:
        cached = self._all_cache
        if not force and cached and self._cache_valid(cached[0]):
            return cached[1]

        services = self.catalog.services()
        results = await asyncio.gather(*(self.check_service(service, force=force) for service in services))
        payload = {
            "checked_count": len([item for item in results if item.status not in {"skipped"}]),
            "up_count": len([item for item in results if item.status in {"up", "reachable_auth_required"}]),
            "down_count": len([item for item in results if item.status == "down"]),
            "skipped_count": len([item for item in results if item.status == "skipped"]),
            "checked_at": _now_iso(),
            "cache_seconds": self.settings.cache_seconds,
            "results": [item.model_dump() for item in results],
        }
        self._all_cache = (time.monotonic() + max(1, self.settings.cache_seconds), payload)
        return payload

    async def check_one_by_name(self, service_name: str, force: bool = False) -> HealthCheckResult | None:
        service = self.catalog.get_service(service_name)
        if service is None:
            service = self.catalog.get_service(slugify(service_name))
        if service is None:
            return None
        return await self.check_service(service, force=force)

    async def down_services(self, critical_only: bool = False) -> dict[str, Any]:
        data = await self.check_all()
        results = data["results"]
        if critical_only:
            results = [
                item
                for item in results
                if item["status"] == "down" and str(item.get("criticality") or "").lower() in {"critical", "high"}
            ]
        else:
            results = [item for item in results if item["status"] == "down"]
        return {"count": len(results), "results": results, "generated_at": _now_iso()}
