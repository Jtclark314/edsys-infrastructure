"""EdSys Control API entrypoint."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .catalog_loader import CatalogStore
from .config import Settings, get_settings
from .health_checks import HealthChecker
from .routers import dashboard, devices, health, meta, search, services


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    catalog = CatalogStore(settings)
    health_checker = HealthChecker(settings, catalog)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        catalog.load(force=True)
        yield

    app = FastAPI(title=settings.api_title, version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.catalog = catalog
    app.state.health_checker = health_checker
    app.state.started_at = time.monotonic()

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", tags=["root"])
    def root() -> dict:
        return {
            "name": settings.api_title,
            "mode": "read-only",
            "source_of_truth": "EdSys-Master YAML",
            "links": {
                "meta": "/api/meta",
                "summary": "/api/summary",
                "services": "/api/services",
                "devices": "/api/devices",
                "search": "/api/search?q=plex",
                "health": "/api/health",
                "live_health": "/api/health/live",
                "dashboard": "/dashboard",
                "openapi": "/docs",
            },
        }

    @app.get("/api/export/services.json", tags=["export"])
    def export_services() -> list[dict]:
        return [service.model_dump() for service in catalog.services()]

    @app.get("/api/export/devices.json", tags=["export"])
    def export_devices() -> list[dict]:
        return [device.model_dump() for device in catalog.devices()]

    app.include_router(meta.router)
    app.include_router(services.router)
    app.include_router(devices.router)
    app.include_router(search.router)
    app.include_router(health.router)
    app.include_router(dashboard.router)
    return app


app = create_app()
