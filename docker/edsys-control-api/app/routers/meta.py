"""Metadata and summary endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request


router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/meta")
def meta(request: Request) -> dict:
    return request.app.state.catalog.meta()


@router.get("/summary")
def summary(request: Request) -> dict:
    return request.app.state.catalog.summary()


@router.get("/categories")
def categories(request: Request) -> dict:
    return request.app.state.catalog.categories()
