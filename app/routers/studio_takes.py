"""Speak + takes API (SPEC §7). M2-T3.

``/api/speak`` synthesizes a native-rate WAV, saves a take and returns
``TakeInfo``; the caller's ``sample_rate``/``speed`` never touch the stored take
(SPEC §5: takes are always 48 kHz). ``stream=true`` returns the audio with
``X-Take-Id`` and writes the take when the stream completes (Q-006), decoding
any streamed container with ``audio.decode_any``.

``save=false`` (Q-023) is the Studio preview / "Test a line" path: the audio is
returned in ``response_format`` under Studio auth, no take is written and no
``X-Take-Id`` is sent. The take's requested container is recorded in its
``format`` field and drives playback via ``?format=`` (Q-024); disk stays WAV.
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
from app.engine import ENGINE_SAMPLE_RATE
from app.errors import APIError, BadRequest
from app.models import SaveAsVoice, SpeechRequest, TakeInfo, VoiceSummary, safe_header_value

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
async def speak(request: Request, payload: SpeechRequest, save: bool = True) -> Response | TakeInfo:
    settings: Any = request.app.state.settings
    if len(payload.input) > settings.max_input_chars:
        raise BadRequest(
            f"Input is too long (max {settings.max_input_chars} characters).",
            param="input",
            code="input_too_long",
        )
    if payload.stream:
        return await _speak_stream(request, payload, save)
    if not save:
        # Q-023 preview: honor response_format, write nothing, no X-Take-Id.
        result = await request.app.state.speech.synthesize(payload)
        return Response(
            result.audio,
            media_type=result.media_type,
            headers={
                "X-Voice": safe_header_value(result.key or result.voice),
                "X-Sample-Rate": str(result.sample_rate),
            },
        )

    # SPEC §5/§7: /api/speak returns take metadata only, so synthesize at the
    # engine's native rate without speed stretch — the stored take is always a
    # 48 kHz, unstretched WAV whatever the caller asked for.
    result = await request.app.state.speech.synthesize(
        payload.model_copy(
            update={
                "response_format": "wav",
                "sample_rate": ENGINE_SAMPLE_RATE,
                "speed": 1.0,
            }
        )
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
        extra={"format": payload.response_format},
    )
    return TakeInfo(**take, **request.app.state.takes.take_urls(take["id"]))


async def _speak_stream(request: Request, payload: SpeechRequest, save: bool = True) -> Response:
    state = request.app.state
    # Admission happens here: QueueFull/EngineNotReady still reach the error handler.
    streaming = await state.speech.stream(payload)
    headers = {
        "X-Voice": safe_header_value(streaming.key or streaming.voice),
        "X-Sample-Rate": str(streaming.sample_rate),
    }
    if not save:
        # Q-023 preview: stream the container through, free the slot, write nothing.
        async def preview() -> Any:
            try:
                async for chunk in streaming.chunks:
                    yield chunk
            finally:
                await streaming.aclose()

        return StreamingResponse(
            preview(),
            media_type=streaming.media_type,
            headers=headers,
            # Disconnect before the first chunk: the idempotent aclose still frees the slot.
            background=BackgroundTask(streaming.aclose),
        )

    take_id: str = state.takes.new_id()
    started = time.perf_counter()
    is_pcm = payload.response_format == "pcm"
    parts: list[bytes] = []
    carry = b""
    first_audio_ms: float | None = None

    def write_take(raw: bytes) -> None:
        try:
            if is_pcm:
                samples, rate = audio.pcm_s16le_to_float(raw), streaming.sample_rate
            else:
                samples, rate = audio.decode_any(raw)
        except Exception:  # noqa: BLE001 - headers are already sent; log, keep the audio
            logger.warning("streamed take %s could not be decoded and was not saved", take_id)
            return
        if rate != ENGINE_SAMPLE_RATE:  # SPEC §5: takes are always 48 kHz
            samples = audio.resample(samples, rate, ENGINE_SAMPLE_RATE)
            rate = ENGINE_SAMPLE_RATE
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
        await asyncio.to_thread(write_take, b"".join(parts))

    return StreamingResponse(
        body(),
        media_type=streaming.media_type,
        headers={"X-Take-Id": take_id, **headers},
        # Disconnect before the first chunk: the idempotent aclose still frees the slot.
        background=BackgroundTask(streaming.aclose),
    )


@router.get("/takes")
async def list_takes(request: Request, kind: str = "studio") -> dict[str, list[TakeInfo]]:
    """Speak Takes list. ``kind=studio`` (default) hides design candidates; ``all`` shows both."""
    takes: Any = request.app.state.takes
    items: list[TakeInfo] = []
    for take in await asyncio.to_thread(takes.list):
        if kind != "all" and take.get("kind", "studio") != kind:
            continue
        try:
            takes.audio_path(str(take.get("id", "")))  # skip entries whose WAV is gone
        except APIError:
            continue
        items.append(TakeInfo(**take, **takes.take_urls(str(take["id"]))))
    return {"takes": items}


@router.get("/takes/{take_id}/audio")
async def take_audio(
    request: Request, take_id: str, format: str = "", sample_rate: int | None = None
) -> Response:
    """Serve a take, transcoded on the fly to ``format`` (Q-024). Default: its stored format."""
    takes: Any = request.app.state.takes
    path = await asyncio.to_thread(takes.audio_path, take_id)  # 404 when the WAV is gone
    fmt = format or str((await asyncio.to_thread(takes.get, take_id)).get("format") or "wav")
    if fmt not in audio.MIME_TYPES:
        raise BadRequest(
            f"Unsupported format '{format}'. Valid formats: {', '.join(audio.MIME_TYPES)}.",
            param="format",
            code="invalid_format",
        )
    if fmt == "wav" and sample_rate is None:
        return FileResponse(path, media_type=audio.mime_for("wav"))
    samples, rate = await asyncio.to_thread(audio.read_audio_file, path)
    data = await asyncio.to_thread(
        audio.transcode, samples, rate, fmt, target_rate=sample_rate, speed=1.0
    )
    return Response(data, media_type=audio.mime_for(fmt))


@router.get("/takes/{take_id}/wave")
async def take_wave(request: Request, take_id: str) -> dict[str, list[float]]:
    amps = await asyncio.to_thread(request.app.state.takes.wave, take_id)
    return {"amps": amps}


@router.post("/takes/{take_id}/save-as-voice", response_model=VoiceSummary)
async def save_as_voice(request: Request, take_id: str, payload: SaveAsVoice) -> VoiceSummary:
    takes: Any = request.app.state.takes
    take = await asyncio.to_thread(takes.get, take_id)
    samples, rate = await asyncio.to_thread(audio.read_audio_file, takes.audio_path(take_id))
    designed = take.get("kind") == "design"
    voice = await asyncio.to_thread(
        request.app.state.store.create,
        payload.name,
        samples,
        rate,
        ref_text=str(take.get("text", "")),
        language=payload.language,
        consent="own-generated-voice",
        source="designed" if designed else "uploaded",
        metadata=(
            {
                "design_prompt": str(take.get("design_prompt", "")),
                "design_seed": str(take.get("design_seed", "")),
            }
            if designed
            else None
        ),
    )
    return VoiceSummary(**voice.summary())


@router.delete("/takes/{take_id}")
async def delete_take(request: Request, take_id: str) -> dict[str, str]:
    request.app.state.takes.delete(take_id)
    return {"deleted": take_id}
