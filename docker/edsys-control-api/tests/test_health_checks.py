from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from app.catalog_loader import normalize_service
from app.config import Settings
from app.health_checks import HealthChecker
from app.models import HealthCheckResult, ServiceEntry


class SampleCatalog:
    def __init__(self, services: list[ServiceEntry]):
        self._services = services

    def services(self) -> list[ServiceEntry]:
        return self._services


def test_explicit_probe_and_lifecycle_results() -> None:
    entries = [
        {"name": "Stopped", "url": "http://example.test", "monitoring_enabled": False},
        {"name": "Host local", "url": "http://127.0.0.1:6071", "health_probe": {"type": "unverified", "reason": "host_local_only"}},
        {"name": "TCP service", "url": "https://example.test", "health_probe": {"type": "tcp", "host": "192.0.2.10", "port": 8006}},
        {"name": "Health path", "url": "http://example.test", "health_probe": {"type": "http", "url": "http://example.test/health"}},
        {"name": "Bad probe", "url": "http://example.test", "health_probe": {"type": "http", "url": "file:///etc/passwd"}},
    ]
    services = [normalize_service(entry) for entry in entries]
    settings = Settings(network_map=Path("unused"), service_catalog=Path("unused"))
    checker = HealthChecker(settings, SampleCatalog(services))  # type: ignore[arg-type]
    targets: list[str] = []

    async def fake_http(service: ServiceEntry, url: str) -> HealthCheckResult:
        targets.append(url)
        return HealthCheckResult(name=service.name, slug=service.slug, status="up", target=url, checked_at="test")

    async def fake_tcp(service: ServiceEntry, target: str, host: str, port: int) -> HealthCheckResult:
        targets.append(target)
        return HealthCheckResult(name=service.name, slug=service.slug, status="up", target=target, checked_at="test")

    checker._check_http = fake_http  # type: ignore[method-assign]
    checker._check_tcp = fake_tcp  # type: ignore[method-assign]
    result = asyncio.run(checker.check_all())

    assert targets == ["192.0.2.10:8006", "http://example.test/health"]
    assert [entry["status"] for entry in result["results"]] == ["skipped", "unverified", "up", "up", "unverified"]
    assert result["checked_count"] == 2
    assert result["up_count"] == 2
    assert result["down_count"] == 0
    assert result["skipped_count"] == 1
    assert result["unverified_count"] == 2
    assert result["results"][0]["reason"] == "monitoring_disabled"
    assert result["results"][4]["reason"] == "invalid_probe_config"


def test_explicit_get_probe_avoids_a_failing_head(monkeypatch) -> None:
    service = normalize_service(
        {
            "name": "GET-only status",
            "url": "http://example.test/",
            "health_probe": {"type": "http", "url": "http://example.test/health", "method": "GET"},
        }
    )
    settings = Settings(network_map=Path("unused"), service_catalog=Path("unused"))
    checker = HealthChecker(settings, SampleCatalog([service]))  # type: ignore[arg-type]
    seen_methods: list[str] = []

    def reply(request: httpx.Request) -> httpx.Response:
        seen_methods.append(request.method)
        return httpx.Response(200 if request.method == "GET" else 404)

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(reply)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs))

    result = asyncio.run(checker.check_service(service))
    assert seen_methods == ["GET"]
    assert result.status == "up"
    assert result.target == "http://example.test/health"
