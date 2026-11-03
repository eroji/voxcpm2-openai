"""OpenAI-compatible ``/v1`` routes (SPEC §6)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from app.auth import require_v1
from app.errors import BadRequest
from app.models import ModelCard, ModelList, SpeechRequest, safe_header_value
from app.speech import SpeechResult, StreamingSpeech, require_warm

router = APIRouter(prefix="/v1", tags=["openai"], dependencies=[Depends(require_v1)])


def _voice_header(seq: StreamingSpeech | SpeechResult) -> str:
    """X-Voice is user-controlled for ad-hoc clones: sanitize or 500/injection (M1-T14)."""
    return safe_header_value(seq.key or seq.voice)


@router.get("/models")
async def models() -> ModelList:
    # SPEC §6: exactly one card; tts-1 / openbmb/VoxCPM2 are accepted aliases, not listed.
    return ModelList(data=[ModelCard(id="voxcpm2")])


@router.get("/audio/voices")
async def voices(request: Request) -> dict:
    return {
        "voices": [
            {"name": v.key, "language": v.language, "source": v.source}
            for v in request.app.state.store.list()
        ]
    }


@router.post("/audio/speech")
async def audio_speech(request: Request, payload: SpeechRequest) -> Response:
    settings = request.app.state.settings
    if len(payload.input) > settings.max_input_chars:
        raise BadRequest(
            f"input exceeds the configured limit of {settings.max_input_chars} characters.",
            param="input",
            code="input_too_long",
        )
    speech = request.app.state.speech
    warmup = request.app.state.warmup
    # SPEC §4: pre-ready requests are 503 and never reach the metrics window.
    require_warm(warmup)
    if payload.stream:
        # Admission happens here: QueueFull/EngineNotReady raise before any header is sent.
        streaming: StreamingSpeech = await speech.stream(payload, record=warmup.warm)

        async def chunks() -> AsyncIterator[bytes]:
            try:
                async for chunk in streaming.chunks:
                    yield chunk
            finally:
                await streaming.aclose()

        return StreamingResponse(
            chunks(),
            media_type=streaming.media_type,
            headers={
                "X-Voice": _voice_header(streaming),
                "X-Sample-Rate": str(streaming.sample_rate),
            },
            # Covers a client that disconnects before the first chunk is pulled;
            # aclose() is idempotent, so the generator's finally may run too.
            background=BackgroundTask(streaming.aclose),
        )

    seq = await speech.synthesize(payload, record=warmup.warm)
    headers = {
        "X-Voice": _voice_header(seq),
        "X-Sample-Rate": str(seq.sample_rate),
        "X-Audio-Duration": f"{seq.duration_s:.3f}",
    }
    if seq.rtf is not None:
        headers["X-RTF"] = f"{seq.rtf:.4f}"
    return Response(seq.audio, media_type=seq.media_type, headers=headers)
