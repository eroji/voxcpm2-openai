"""Engine status, warm-up and restart API (SPEC §7)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

from app.errors import EngineError, EngineNotReady
from app.models import EngineStatus

router = APIRouter(prefix="/api", tags=["engine"])

# One restart at a time: two clicks must not interleave reset / restart / warm-up (M2-T16).
_restart_lock = asyncio.Lock()


def _mark_failed(request: Request, message: str, step: int) -> None:
    """Show why the attempt died instead of leaving the page on 'starting' forever."""
    startup = getattr(request.app.state, "startup", None)
    if startup is not None:
        startup.mark_failed(message, step)


@router.get("/engine")
async def engine_status(request: Request) -> EngineStatus:
    engine = request.app.state.engine
    warmup = request.app.state.warmup
    store = request.app.state.store
    settings = request.app.state.settings
    # stats() shells out to nvidia-smi — keep it off the event loop.
    stats = await asyncio.to_thread(engine.stats)
    return EngineStatus(
        ready=warmup.warm,  # warm, not merely engine liveness
        reason=warmup.last_error,
        model=stats.model,
        runtime=stats.runtime,
        gpu=stats.gpu,
        vram_used_mb=stats.vram_used_mb,
        vram_total_mb=stats.vram_total_mb,
        kv_cache=stats.kv_cache,
        max_concurrency=stats.max_concurrency,
        rtf=stats.rtf,
        first_audio_ms=stats.first_audio_ms,
        voices=len(store.list()),
        queue_depth=stats.queue_depth,
        stt=bool(settings.stt_url),
        api_keys_required=bool(settings.api_key_set),
    )


@router.post("/engine/warmup")
async def engine_warmup(request: Request) -> dict[str, bool]:
    warmup = request.app.state.warmup
    if not await warmup.run():
        message = warmup.last_error or "engine warm-up failed"
        _mark_failed(request, message, step=3)
        raise EngineNotReady(message)
    return {"warm": True}


@router.get("/engine/startup")
async def engine_startup(request: Request) -> dict:
    # Always available (startup gate exception): snapshot reads the log + stats off-loop.
    return await asyncio.to_thread(request.app.state.startup.snapshot)


@router.post("/engine/restart")
async def engine_restart(request: Request) -> dict[str, bool]:
    warmup = request.app.state.warmup
    startup = getattr(request.app.state, "startup", None)
    async with _restart_lock:
        warmup.warm = False  # before the reset: never show "ready" mid-restart
        if startup is not None:
            startup.reset()  # page carries on polling from step 2 (ui/STARTING.md)
        try:
            await request.app.state.engine.restart()  # EngineError -> 502 via handler
        except EngineError as exc:
            _mark_failed(request, str(exc), step=2)
            raise
        if not await warmup.run():
            message = warmup.last_error or "engine warm-up failed"
            _mark_failed(request, message, step=3)
            raise EngineNotReady(message)
    return {"restarted": True, "warm": True}
