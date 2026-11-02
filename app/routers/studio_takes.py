"""Speak + takes API (SPEC §7). M2-T3.

``/api/speak`` synthesizes a native-rate WAV, saves a take and returns
``TakeInfo``; the caller's ``sample_rate``/``speed`` never touch the stored take
(SPEC §5: takes are always 48 kHz). ``stream=true`` returns the audio with
``X-Take-Id`` and writes the take when the stream completes (Q-006), decoding
any streamed container with ``audio.decode_any``.
"""

from __future__ import annotations

import asyncio
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


def _language(store: Any, name: str, adhoc: bool) -> str:
    """Language of the voice keyed by ``name``; ad-hoc ``ref_audio`` labels resolve to ""."""
    if adhoc:
        return ""
    try:
        return store.get(name).language
    except APIError:
        # Backward compatibility for callers that passed the display name.
        return next((v.language for v in store.list() if v.name == name), "")


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

    # SPEC §5/§7: /api/speak returns take metadata only, so synthesize at the
    # engine's native rate without speed stretch — the stored take is always a
    # 48 kHz, unstretched WAV whatever the caller asked for.
    result = await request.app.state.speech.synthesize(
        payload.model_copy(update={"response_format": "wav", "sample_rate": None, "speed": 1.0})
    )
    samples, rate = audio.read_audio(result.audio)
    language = await asyncio.to_thread(
        _language, request.app.state.store, payload.voice, bool(payload.ref_audio)
    )
    take = await asyncio.to_thread(
        request.app.state.takes.create,
        samples,
        rate,
        voice=result.voice,
        text=payload.input,
        language=language,
        rtf=result.rtf,
        first_audio_ms=result.first_audio_ms,
    )
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

    def save(raw: bytes) -> None:
        try:
            if is_pcm:
                samples, rate = audio.pcm_s16le_to_float(raw), streaming.sample_rate
            else:
                samples, rate = audio.decode_any(raw)
        except Exception:  # noqa: BLE001 - headers are already sent; log, keep the audio
            logger.warning("streamed take %s could not be decoded and was not saved", take_id)
            return
        if rate != state.settings.sample_rate:  # SPEC §5: takes are always 48 kHz
            samples = audio.resample(samples, rate, state.settings.sample_rate)
            rate = state.settings.sample_rate
        duration = audio.duration_s(samples, rate)
        wall = time.perf_counter() - started
        state.takes.create(
            samples,
            rate,
            voice=streaming.voice,
            text=payload.input,
            language=_language(state.store, payload.voice, bool(payload.ref_audio)),
            rtf=wall / duration if duration > 0 else None,
            first_audio_ms=first_audio_ms,
            take_id=take_id,
        )

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
        await asyncio.to_thread(save, b"".join(parts))

    # Headers must stay ASCII: use the request key, never a display name (SPEC §5).
    voice_header = payload.voice if payload.voice.isascii() else "ad-hoc"
    return StreamingResponse(
        body(),
        media_type=streaming.media_type,
        headers={
            "X-Take-Id": take_id,
            "X-Voice": voice_header,
            "X-Sample-Rate": str(streaming.sample_rate),
        },
        # Disconnect before the first chunk: the idempotent aclose still frees the slot.
        background=BackgroundTask(streaming.aclose),
    )


@router.get("/takes")
async def list_takes(request: Request) -> dict[str, list[TakeInfo]]:
    takes: Any = request.app.state.takes
    items: list[TakeInfo] = []
    for take in await asyncio.to_thread(takes.list):
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
    amps = await asyncio.to_thread(request.app.state.takes.wave, take_id)
    return {"amps": amps}


@router.post("/takes/{take_id}/save-as-voice", response_model=VoiceSummary)
async def save_as_voice(request: Request, take_id: str, payload: SaveAsVoice) -> VoiceSummary:
    takes: Any = request.app.state.takes
    take = await asyncio.to_thread(takes.get, take_id)
    samples, rate = await asyncio.to_thread(audio.read_audio_file, takes.audio_path(take_id))
    voice = await asyncio.to_thread(
        request.app.state.store.create,
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
