"""OpenAI-compatible ``/v1`` routes (SPEC §6)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from app.auth import require_v1
from app.errors import BadRequest
from app.models import ModelCard, ModelList, SpeechRequest
from app.speech import StreamingSpeech

router = APIRouter(prefix="/v1", tags=["openai"], dependencies=[Depends(require_v1)])


@router.get("/models")
async def models() -> ModelList:
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
    if payload.stream:
        # Admission happens here: QueueFull/EngineNotReady raise before any header is sent.
        streaming: StreamingSpeech = await speech.stream(payload)

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
                "X-Voice": streaming.voice,
                "X-Sample-Rate": str(streaming.sample_rate),
            },
            # Covers a client that disconnects before the first chunk is pulled;
            # aclose() is idempotent, so the generator's finally may run too.
            background=BackgroundTask(streaming.aclose),
        )

    seq = await speech.synthesize(payload)
    headers = {
        "X-Voice": seq.voice,
        "X-Sample-Rate": str(seq.sample_rate),
        "X-Audio-Duration": f"{seq.duration_s:.3f}",
    }
    if seq.rtf is not None:
        headers["X-RTF"] = f"{seq.rtf:.4f}"
    return Response(seq.audio, media_type=seq.media_type, headers=headers)
