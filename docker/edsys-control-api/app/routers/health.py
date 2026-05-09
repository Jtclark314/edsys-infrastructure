"""API and service health endpoints."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request


router = APIRouter(prefix="/api/health", tags=["health"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("")
def api_health(request: Request) -> dict:
    data = request.app.state.catalog.load()
    return {
        "api_status": "ok",
        "yaml_loaded": True,
        "service_catalog_loaded": True,
        "network_map_loaded": True,
        "service_count": len(data.services),
        "device_count": len(data.devices),
        "uptime_seconds": round(time.monotonic() - request.app.state.started_at, 2),
        "timestamp": _now_iso(),
    }


@router.get("/live")
async def live_health(request: Request, force: bool = False) -> dict:
    return await request.app.state.health_checker.check_all(force=force)


@router.get("/down")
async def down_services(request: Request) -> dict:
    return await request.app.state.health_checker.down_services()


@router.get("/critical-down")
async def critical_down_services(request: Request) -> dict:
    return await request.app.state.health_checker.down_services(critical_only=True)


@router.get("/live/{service_name}")
async def live_health_for_service(service_name: str, request: Request, force: bool = False) -> dict:
    result = await request.app.state.health_checker.check_one_by_name(service_name, force=force)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Service not found: {service_name}")
    return result.model_dump()
