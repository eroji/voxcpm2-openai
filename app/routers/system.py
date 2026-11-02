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
    engine = getattr(request.app.state, "engine", None)
    warm = bool(warmup is not None and warmup.warm)
    alive = bool(getattr(engine, "ready", False))
    if warm and alive:
        return JSONResponse({"status": "ready"}, status_code=200)
    reason = None
    if warm and not alive:
        reason = "engine is not ready"
    elif warmup is not None:
        reason = warmup.last_error or "engine is not ready"
    return JSONResponse(
        {"status": "warming", "error": reason},
        status_code=503,
        headers={"Retry-After": "5"},
    )
