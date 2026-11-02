"""Speak + takes API (SPEC §7). M2-T3.

Non-stream ``/api/speak`` synthesizes WAV, saves a take and returns ``TakeInfo``;
a non-WAV ``response_format`` returns the transcoded audio bytes instead (the
take on disk stays WAV). ``stream=true`` returns the audio with ``X-Take-Id``
and writes the take when the stream completes (Q-006).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from app import audio
from app.errors import APIError, BadRequest
from app.models import SaveAsVoice, SpeechRequest, TakeInfo, VoiceSummary

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["takes"])


def _language(store: Any, name: str) -> str:
    """Language of a stored voice; ad-hoc ``ref_audio`` labels resolve to "" (SPEC §6)."""
    return store.get(name).language if store.exists(name) else ""


@router.post("/speak", response_model=None)
async def speak(request: Request, payload: SpeechRequest) -> Response | TakeInfo:
    settings: Any = request.app.state.settings
    if len(payload.input) > settings.max_input_chars:
        raise BadRequest(
            f"Input is too long (max {settings.max_input_chars} characters).",
            param="input",
            code="input_too_long",
        )
    if payload.stream:
        return await _speak_stream(request, payload)

    result = await request.app.state.speech.synthesize(
        payload.model_copy(update={"response_format": "wav"})
    )
    samples, rate = audio.read_audio(result.audio)
    take = request.app.state.takes.create(
        samples,
        rate,
        voice=result.voice,
        text=payload.input,
        language=_language(request.app.state.store, result.voice),
        rtf=result.rtf,
        first_audio_ms=result.first_audio_ms,
    )
    # SPEC §7: /api/speak returns take metadata + an audio URL (the take is always WAV).
    return TakeInfo(**take, **request.app.state.takes.take_urls(take["id"]))


async def _speak_stream(request: Request, payload: SpeechRequest) -> Response:
    state = request.app.state
    take_id: str = state.takes.new_id()
    started = time.perf_counter()
    # Admission happens here: QueueFull/EngineNotReady still reach the error handler.
    streaming = await state.speech.stream(payload)
    is_pcm = payload.response_format == "pcm"
    parts: list[bytes] = []
    carry = b""
    first_audio_ms: float | None = None

    async def body() -> Any:
        nonlocal carry, first_audio_ms
        try:
            async for chunk in streaming.chunks:
                if first_audio_ms is None:
                    first_audio_ms = (time.perf_counter() - started) * 1000.0
                if is_pcm:
                    data = carry + chunk
                    usable = len(data) - len(data) % 2  # s16le frames only; carry 1 stray byte
                    parts.append(data[:usable])
                    carry = data[usable:]
                else:
                    parts.append(chunk)
                yield chunk
        finally:
            await streaming.aclose()
        raw = b"".join(parts)
        try:
            if is_pcm:
                samples, rate = audio.pcm_s16le_to_float(raw), streaming.sample_rate
            else:
                samples, rate = audio.read_audio(raw)
        except Exception:  # noqa: BLE001 - headers are already sent; log, keep the audio
            logger.warning("streamed take %s could not be decoded and was not saved", take_id)
            return
        duration = audio.duration_s(samples, rate)
        wall = time.perf_counter() - started
        state.takes.create(
            samples,
            rate,
            voice=streaming.voice,
            text=payload.input,
            language=_language(state.store, streaming.voice),
            rtf=wall / duration if duration > 0 else None,
            first_audio_ms=first_audio_ms,
            take_id=take_id,
        )

    return StreamingResponse(
        body(),
        media_type=streaming.media_type,
        headers={
            "X-Take-Id": take_id,
            "X-Voice": streaming.voice,
            "X-Sample-Rate": str(streaming.sample_rate),
        },
        # Disconnect before the first chunk: the idempotent aclose still frees the slot.
        background=BackgroundTask(streaming.aclose),
    )


@router.get("/takes")
async def list_takes(request: Request) -> dict[str, list[TakeInfo]]:
    takes: Any = request.app.state.takes
    items: list[TakeInfo] = []
    for take in takes.list():
        try:
            takes.audio_path(str(take.get("id", "")))  # skip entries whose WAV is gone
        except APIError:
            continue
        items.append(TakeInfo(**take, **takes.take_urls(str(take["id"]))))
    return {"takes": items}


@router.get("/takes/{take_id}/audio")
async def take_audio(request: Request, take_id: str) -> FileResponse:
    return FileResponse(request.app.state.takes.audio_path(take_id), media_type="audio/wav")


@router.get("/takes/{take_id}/wave")
async def take_wave(request: Request, take_id: str) -> dict[str, list[float]]:
    return {"amps": request.app.state.takes.wave(take_id)}


@router.post("/takes/{take_id}/save-as-voice", response_model=VoiceSummary)
async def save_as_voice(request: Request, take_id: str, payload: SaveAsVoice) -> VoiceSummary:
    takes: Any = request.app.state.takes
    take = takes.get(take_id)
    samples, rate = audio.read_audio_file(takes.audio_path(take_id))
    voice = request.app.state.store.create(
        payload.name,
        samples,
        rate,
        ref_text=str(take.get("text", "")),
        language=payload.language,
        consent="own-generated-voice",
        source="uploaded",
    )
    return VoiceSummary(**voice.summary())


@router.delete("/takes/{take_id}")
async def delete_take(request: Request, take_id: str) -> dict[str, str]:
    request.app.state.takes.delete(take_id)
    return {"deleted": take_id}
