"""Dashboard route for the static human view."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse


router = APIRouter(tags=["dashboard"])
STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


@router.get("/dashboard")
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
