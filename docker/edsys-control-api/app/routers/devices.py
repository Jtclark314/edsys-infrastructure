"""Device inventory endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..catalog_loader import text_matches


router = APIRouter(prefix="/api/devices", tags=["devices"])


def _matches_filter(actual: Any, expected: str | None) -> bool:
    if expected is None:
        return True
    return str(actual or "").lower() == expected.lower()


@router.get("")
def list_devices(
    request: Request,
    category: str | None = None,
    status: str | None = None,
    q: str | None = None,
) -> list[dict]:
    devices = request.app.state.catalog.devices()
    output = []
    for device in devices:
        item = device.model_dump()
        if not _matches_filter(device.category, category):
            continue
        if not _matches_filter(device.status, status):
            continue
        if q and not text_matches(
            item,
            q,
            ["hostname", "slug", "aliases", "ip", "category", "notes", "mac", "role", "tailscale_ip"],
        ):
            continue
        output.append(item)
    return output


@router.get("/{hostname}/services")
def services_for_device(hostname: str, request: Request) -> dict:
    device = request.app.state.catalog.get_device(hostname)
    if device is None:
        raise HTTPException(status_code=404, detail=f"Device not found: {hostname}")
    services = request.app.state.catalog.services_for_device(hostname)
    return {"device": device.model_dump(), "services": [service.model_dump() for service in services]}


@router.get("/{hostname}")
def get_device(hostname: str, request: Request) -> dict:
    device = request.app.state.catalog.get_device(hostname)
    if device is None:
        raise HTTPException(status_code=404, detail=f"Device not found: {hostname}")
    return device.model_dump()
