from __future__ import annotations

from pathlib import Path

import yaml

from app.catalog_loader import CatalogStore, backup_is_required, normalize_device, normalize_service, slugify
from app.config import Settings


def _write_sample_catalog(tmp_path: Path) -> Settings:
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
                    },
                    {"hostname": "edcore", "category": "network-core", "ip": "192.168.50.1"},
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
                        "criticality": "medium",
                        "backup_required": True,
                    },
                    {"name": "Router DNS", "host": "edcore", "criticality": "critical", "backup_required": "config backup required"},
                    {"name": "Optional Cache", "host": "9950x", "backup_required": False},
                ],
            }
        ),
        encoding="utf-8",
    )
    return Settings(
        network_map=network_map,
        service_catalog=service_catalog,
        enable_live_checks=False,
        cache_seconds=1,
    )


def test_slugify_is_stable() -> None:
    assert slugify("Open WebUI") == "open-webui"
    assert slugify("  EdSys Control / Status API  ") == "edsys-control-status-api"


def test_catalog_loads_sample_yaml(tmp_path: Path) -> None:
    store = CatalogStore(_write_sample_catalog(tmp_path))
    data = store.load()
    assert len(data.devices) == 2
    assert len(data.services) == 3
    assert store.get_service("open-webui").name == "Open WebUI"
    assert store.get_device("primary-docker-host").hostname == "9950x"
    assert len(store.services_for_device("9950x")) == 2


def test_missing_optional_fields_do_not_crash() -> None:
    service = normalize_service({"name": "Tiny Service"})
    device = normalize_device({"hostname": "tiny-host"})
    assert service.slug == "tiny-service"
    assert service.dependencies == []
    assert service.mounts == []
    assert device.slug == "tiny-host"
    assert device.aliases == []


def test_backup_required_string_handling() -> None:
    assert backup_is_required(True) is True
    assert backup_is_required("config backup required") is True
    assert backup_is_required(False) is False
    assert backup_is_required("not required") is False
