"""Service catalog endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from ..catalog_loader import backup_is_required, text_matches


router = APIRouter(prefix="/api/services", tags=["services"])


def _matches_filter(actual: Any, expected: str | None) -> bool:
    if expected is None:
        return True
    return str(actual or "").lower() == expected.lower()


@router.get("")
def list_services(
    request: Request,
    category: str | None = None,
    host: str | None = None,
    criticality: str | None = None,
    status: str | None = None,
    backup_required: str | None = Query(default=None),
    q: str | None = None,
) -> list[dict]:
    services = request.app.state.catalog.services()
    output = []
    for service in services:
        item = service.model_dump()
        if not _matches_filter(service.category, category):
            continue
        if not _matches_filter(service.host, host):
            continue
        if not _matches_filter(service.criticality, criticality):
            continue
        if not _matches_filter(service.status, status):
            continue
        if backup_required is not None:
            wants_backup = backup_required.strip().lower() in {"1", "true", "yes"}
            if backup_is_required(service.backup_required) != wants_backup:
                continue
        if q and not text_matches(
            item,
            q,
            ["name", "slug", "host", "ip", "category", "notes", "url", "container_name", "image"],
        ):
            continue
        output.append(item)
    return output


@router.get("/critical")
def critical_services(request: Request) -> list[dict]:
    return [
        service.model_dump()
        for service in request.app.state.catalog.services()
        if str(service.criticality or "").lower() in {"critical", "high"}
    ]


@router.get("/backup-required")
def backup_required_services(request: Request) -> list[dict]:
    return [
        service.model_dump()
        for service in request.app.state.catalog.services()
        if backup_is_required(service.backup_required)
    ]


@router.get("/{service_name}")
def get_service(service_name: str, request: Request) -> dict:
    service = request.app.state.catalog.get_service(service_name)
    if service is None:
        raise HTTPException(status_code=404, detail=f"Service not found: {service_name}")
    return service.model_dump()
