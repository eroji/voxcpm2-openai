"""Liveness/readiness endpoints (SPEC §7)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    warmup = getattr(request.app.state, "warmup", None)
    if warmup is not None and warmup.warm:
        return JSONResponse({"status": "ready"}, status_code=200)
    return JSONResponse(
        {"status": "warming", "error": getattr(warmup, "last_error", None)},
        status_code=503,
        headers={"Retry-After": "5"},
    )
