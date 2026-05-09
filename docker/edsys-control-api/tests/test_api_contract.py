from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _settings(tmp_path: Path) -> Settings:
    network_map = tmp_path / "network-map.yml"
    service_catalog = tmp_path / "service-catalog.yml"
    network_map.write_text(
        yaml.safe_dump(
            {
                "metadata": {"title": "Test Network"},
                "devices": [
                    {
                        "hostname": "9950x",
                        "aliases": ["primary-docker-host"],
                        "category": "compute-service-host",
                        "ip": "192.168.50.50",
                        "status": "verified-live",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    service_catalog.write_text(
        yaml.safe_dump(
            {
                "metadata": {"title": "Test Services"},
                "services": [
                    {
                        "name": "Open WebUI",
                        "category": "ai",
                        "host": "9950x",
                        "ip": "192.168.50.50",
                        "port": 3000,
                        "url": "http://192.168.50.50:3000",
                        "criticality": "high",
                        "status": "verified-live",
                        "backup_required": True,
                        "verify_live": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return Settings(network_map=network_map, service_catalog=service_catalog, enable_live_checks=False, cache_seconds=1)


def test_summary_and_catalog_endpoints(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        summary = client.get("/api/summary")
        assert summary.status_code == 200
        data = summary.json()
        assert data["total_services"] == 1
        assert data["total_devices"] == 1
        assert "services_by_category" in data
        assert "critical_services" in data

        assert client.get("/api/services").json()[0]["slug"] == "open-webui"
        assert client.get("/api/services/open-webui").json()["name"] == "Open WebUI"
        assert client.get("/api/devices").json()[0]["hostname"] == "9950x"
        assert client.get("/api/devices/primary-docker-host").json()["hostname"] == "9950x"
        assert client.get("/api/devices/9950x/services").json()["services"][0]["name"] == "Open WebUI"


def test_health_live_skips_when_disabled(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        payload = client.get("/api/health/live").json()
        assert payload["checked_count"] == 0
        assert payload["skipped_count"] == 1
        assert payload["results"][0]["reason"] == "live_checks_disabled"


def test_search_endpoint(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        payload = client.get("/api/search?q=webui").json()
        assert payload["services"][0]["name"] == "Open WebUI"
