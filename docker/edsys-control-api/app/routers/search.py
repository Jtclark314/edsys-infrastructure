"""Cross-catalog search endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request


router = APIRouter(prefix="/api", tags=["search"])


@router.get("/search")
def search(request: Request, q: str = Query(..., min_length=1)) -> dict:
    return request.app.state.catalog.search(q)
