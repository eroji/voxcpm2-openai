"""Voice library API (SPEC §7). M2-T4."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request, Response

from app import audio
from app.config import Settings
from app.errors import BadRequest
from app.importer import import_qwen3_speakers
from app.models import VoicePatch, VoiceSummary
from app.store import VoiceStore

router = APIRouter(prefix="/api", tags=["voices"])


@router.get("/voices")
async def list_voices(request: Request) -> dict[str, list[dict[str, Any]]]:
    store: VoiceStore = request.app.state.store
    return {"voices": [voice.summary() for voice in store.list()]}


@router.get("/voices/{name}/audio")
async def voice_audio(name: str, request: Request) -> Response:
    store: VoiceStore = request.app.state.store
    samples, rate = store.read_audio(name)
    return Response(audio.wav_bytes(samples, rate), media_type="audio/wav")


@router.patch("/voices/{name}", response_model=VoiceSummary)
async def patch_voice(name: str, patch: VoicePatch, request: Request) -> dict[str, Any]:
    store: VoiceStore = request.app.state.store
    fields: dict[str, Any] = {}
    if patch.name is not None:
        fields["new_name"] = patch.name
    if patch.ref_text is not None:
        fields["ref_text"] = patch.ref_text
    if patch.language is not None:
        fields["language"] = patch.language
    if not fields:
        raise BadRequest("Nothing to update.", param="body", code="empty_patch")
    return (await asyncio.to_thread(store.update, name, **fields)).summary()


@router.delete("/voices/{name}")
async def delete_voice(name: str, request: Request) -> dict[str, str]:
    store: VoiceStore = request.app.state.store
    await asyncio.to_thread(store.delete, name)
    return {"deleted": name}


@router.post("/voices/import")
async def import_voices(request: Request, overwrite: bool = False) -> dict[str, list[Any]]:
    settings: Settings = request.app.state.settings
    return await asyncio.to_thread(
        import_qwen3_speakers, request.app.state.store, settings.import_dir, overwrite=overwrite
    )
