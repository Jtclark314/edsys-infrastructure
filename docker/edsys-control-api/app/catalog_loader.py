"""Load, normalize, and query EdSys source-of-truth YAML."""

from __future__ import annotations

import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .config import Settings
from .models import CatalogData, DeviceEntry, ServiceEntry


class CatalogLoadError(RuntimeError):
    """Raised when source-of-truth YAML cannot be loaded."""


SERVICE_FIELDS = {
    "name",
    "slug",
    "category",
    "host",
    "ip",
    "port",
    "url",
    "protocol",
    "runtime",
    "owner",
    "criticality",
    "status",
    "backup_required",
    "dependencies",
    "confidence",
    "verify_live",
    "source",
    "notes",
    "container_name",
    "image",
    "published_ports",
    "mounts",
}

DEVICE_FIELDS = {
    "hostname",
    "slug",
    "aliases",
    "role",
    "category",
    "ip",
    "mac",
    "os",
    "interfaces",
    "primary_services",
    "management_urls",
    "reachable_ports",
    "status",
    "confidence",
    "source",
    "notes",
    "tailscale_ip",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): make_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [make_jsonable(v) for v in value]
    return value


def slugify(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "unnamed"


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def text_matches(entry: dict[str, Any], query: str, fields: list[str]) -> bool:
    q = query.lower()
    for field in fields:
        value = entry.get(field)
        if value is None:
            continue
        if q in str(value).lower():
            return True
    return False


def backup_is_required(value: Any) -> bool:
    if value is True:
        return True
    if value in (False, None):
        return False
    text = str(value).strip().lower()
    if not text:
        return False
    if text in {"false", "no", "none", "n/a", "not required", "optional"}:
        return False
    return True


def normalize_service(raw_entry: dict[str, Any]) -> ServiceEntry:
    raw = make_jsonable(raw_entry or {})
    name = str(raw.get("name") or raw.get("container_name") or raw.get("host") or "unnamed-service")
    normalized: dict[str, Any] = {
        "name": name,
        "slug": slugify(name),
        "category": raw.get("category"),
        "host": raw.get("host"),
        "ip": raw.get("ip"),
        "port": raw.get("port"),
        "url": raw.get("url"),
        "protocol": raw.get("protocol"),
        "runtime": raw.get("runtime"),
        "owner": raw.get("owner"),
        "criticality": raw.get("criticality"),
        "status": raw.get("status"),
        "backup_required": raw.get("backup_required"),
        "dependencies": as_list(raw.get("dependencies")),
        "confidence": raw.get("confidence"),
        "verify_live": raw.get("verify_live"),
        "source": raw.get("source"),
        "notes": raw.get("notes"),
        "container_name": raw.get("container_name"),
        "image": raw.get("image"),
        "published_ports": raw.get("published_ports"),
        "mounts": as_list(raw.get("mounts")),
    }
    normalized["extra"] = {key: value for key, value in raw.items() if key not in SERVICE_FIELDS}
    return ServiceEntry(**normalized)


def normalize_device(raw_entry: dict[str, Any]) -> DeviceEntry:
    raw = make_jsonable(raw_entry or {})
    hostname = str(raw.get("hostname") or raw.get("ip") or "unnamed-device")
    normalized: dict[str, Any] = {
        "hostname": hostname,
        "slug": slugify(hostname),
        "aliases": [str(alias) for alias in as_list(raw.get("aliases"))],
        "role": raw.get("role"),
        "category": raw.get("category"),
        "ip": raw.get("ip"),
        "mac": raw.get("mac"),
        "os": raw.get("os"),
        "interfaces": as_list(raw.get("interfaces")),
        "primary_services": as_list(raw.get("primary_services")),
        "management_urls": as_list(raw.get("management_urls")),
        "reachable_ports": as_list(raw.get("reachable_ports")),
        "status": raw.get("status"),
        "confidence": raw.get("confidence"),
        "source": raw.get("source"),
        "notes": raw.get("notes"),
        "tailscale_ip": raw.get("tailscale_ip"),
    }
    normalized["extra"] = {key: value for key, value in raw.items() if key not in DEVICE_FIELDS}
    return DeviceEntry(**normalized)


def load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise CatalogLoadError(f"YAML file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise CatalogLoadError(f"Invalid YAML in {path}: {exc}") from exc
    except OSError as exc:
        raise CatalogLoadError(f"Could not read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CatalogLoadError(f"Expected top-level mapping in {path}")
    return make_jsonable(data)


class CatalogStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._cached_data: CatalogData | None = None
        self._cached_until = 0.0

    def load(self, force: bool = False) -> CatalogData:
        now = time.monotonic()
        if not force and self._cached_data is not None and now < self._cached_until:
            return self._cached_data

        network = load_yaml_file(self.settings.network_map)
        service_catalog = load_yaml_file(self.settings.service_catalog)
        devices = [normalize_device(item) for item in as_list(network.get("devices")) if isinstance(item, dict)]
        services = [normalize_service(item) for item in as_list(service_catalog.get("services")) if isinstance(item, dict)]
        data = CatalogData(
            network_metadata=make_jsonable(network.get("metadata") or {}),
            service_metadata=make_jsonable(service_catalog.get("metadata") or {}),
            devices=devices,
            services=services,
            loaded_at=utc_now_iso(),
        )
        self._cached_data = data
        self._cached_until = now + max(1, self.settings.cache_seconds)
        return data

    def services(self) -> list[ServiceEntry]:
        return self.load().services

    def devices(self) -> list[DeviceEntry]:
        return self.load().devices

    def meta(self) -> dict[str, Any]:
        data = self.load()
        return {
            "network_map": data.network_metadata,
            "service_catalog": data.service_metadata,
            "loaded_at": data.loaded_at,
        }

    def categories(self) -> dict[str, list[str]]:
        data = self.load()
        service_categories = sorted({item.category for item in data.services if item.category})
        device_categories = sorted({item.category for item in data.devices if item.category})
        return {"service_categories": service_categories, "device_categories": device_categories}

    def get_service(self, service_name: str) -> ServiceEntry | None:
        requested = service_name.lower()
        requested_slug = slugify(service_name)
        for service in self.services():
            if service.name.lower() == requested or service.slug == requested_slug:
                return service
        return None

    def get_device(self, hostname: str) -> DeviceEntry | None:
        requested = hostname.lower()
        requested_slug = slugify(hostname)
        for device in self.devices():
            if device.hostname.lower() == requested or device.slug == requested_slug:
                return device
            if requested in {alias.lower() for alias in device.aliases}:
                return device
            if requested_slug in {slugify(alias) for alias in device.aliases}:
                return device
        return None

    def services_for_device(self, hostname: str) -> list[ServiceEntry]:
        device = self.get_device(hostname)
        if device is None:
            return []
        host_names = {device.hostname.lower(), device.slug}
        host_names.update(alias.lower() for alias in device.aliases)
        host_names.update(slugify(alias) for alias in device.aliases)
        device_ips = {str(device.ip)} if device.ip is not None else set()
        if device.tailscale_ip:
            device_ips.add(str(device.tailscale_ip))

        matches: list[ServiceEntry] = []
        for service in self.services():
            service_host = str(service.host or "").lower()
            service_host_slug = slugify(service.host)
            service_ip = str(service.ip) if service.ip is not None else ""
            if service_host in host_names or service_host_slug in host_names or service_ip in device_ips:
                matches.append(service)
        return matches

    def search(self, query: str) -> dict[str, list[dict[str, Any]]]:
        q = query.strip()
        if not q:
            return {"services": [], "devices": []}
        service_fields = ["name", "slug", "host", "ip", "category", "notes", "url", "container_name", "image"]
        device_fields = ["hostname", "slug", "aliases", "ip", "category", "notes", "mac", "role", "tailscale_ip"]
        services = [
            service.model_dump()
            for service in self.services()
            if text_matches(service.model_dump(), q, service_fields)
        ]
        devices = [
            device.model_dump()
            for device in self.devices()
            if text_matches(device.model_dump(), q, device_fields)
        ]
        return {"services": services, "devices": devices}

    def summary(self) -> dict[str, Any]:
        data = self.load()
        services = data.services
        devices = data.devices

        def count_by(items: list[Any], attr: str) -> dict[str, int]:
            counts: dict[str, int] = {}
            for item in items:
                value = getattr(item, attr, None) or "unspecified"
                counts[str(value)] = counts.get(str(value), 0) + 1
            return dict(sorted(counts.items()))

        critical_services = [
            service.model_dump()
            for service in services
            if str(service.criticality or "").lower() in {"critical", "high"}
        ]
        backup_required_services = [
            service.model_dump()
            for service in services
            if backup_is_required(service.backup_required)
        ]
        missing_backup_info = [
            service.model_dump()
            for service in services
            if service.backup_required is None or str(service.backup_required).strip() == ""
        ]
        verify_live_services = [service.model_dump() for service in services if service.verify_live is True]

        return {
            "total_services": len(services),
            "total_devices": len(devices),
            "services_by_category": count_by(services, "category"),
            "devices_by_category": count_by(devices, "category"),
            "services_by_criticality": count_by(services, "criticality"),
            "services_requiring_backup": {
                "count": len(backup_required_services),
                "services": backup_required_services,
            },
            "services_missing_backup_info": {
                "count": len(missing_backup_info),
                "services": missing_backup_info,
            },
            "services_with_verify_live_true": {
                "count": len(verify_live_services),
                "services": verify_live_services,
            },
            "critical_services": critical_services,
            "generated_at": utc_now_iso(),
        }
