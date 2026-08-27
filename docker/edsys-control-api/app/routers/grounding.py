"""Authenticated, content-nonpersistent EdSys grounding search."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..grounding import GroundingIndex

router = APIRouter(prefix="/api/grounding", tags=["grounding"])


class GroundingSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=4000)
    limit: int = Field(default=6, ge=1, le=12)


def _authorize(request: Request, authorization: str | None) -> None:
    expected = request.app.state.settings.grounding_bearer_token or ""
    supplied = (
        authorization[7:]
        if authorization and authorization.startswith("Bearer ")
        else ""
    )
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="grounding auth unavailable",
        )
    if not hmac.compare_digest(expected, supplied):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized"
        )


@router.post("/search")
def grounding_search(
    payload: GroundingSearchRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    _authorize(request, authorization)
    settings = request.app.state.settings
    index = GroundingIndex(
        settings.grounding_index, settings.grounding_freshness_seconds
    )
    index_status = index.status()
    if not index_status.get("available") or not index_status.get("fresh"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=index_status.get("reason") or "grounding unavailable",
        )
    results = index.search(payload.query, min(payload.limit, settings.grounding_top_k))
    return {
        "schema": "edsys.grounding-search.response.v1",
        "status": index_status,
        "results": results,
        "content_persisted": False,
    }
