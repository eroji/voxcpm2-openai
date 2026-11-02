"""Engine status, warm-up and restart API (SPEC §7)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

from app.errors import EngineNotReady
from app.models import EngineStatus

router = APIRouter(prefix="/api", tags=["engine"])


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
        raise EngineNotReady(warmup.last_error or "engine warm-up failed")
    return {"warm": True}


@router.post("/engine/restart")
async def engine_restart(request: Request) -> dict[str, bool]:
    warmup = request.app.state.warmup
    warmup.warm = False
    await request.app.state.engine.restart()  # EngineError -> 502 via handler
    if not await warmup.run():
        raise EngineNotReady(warmup.last_error or "engine warm-up failed")
    return {"restarted": True, "warm": True}
