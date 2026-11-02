"""Clone + transcribe API (SPEC §7, §10). Implemented by M2-T2."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, File, Form, Request, UploadFile

from app import audio
from app.config import Settings
from app.errors import APIError, BadRequest
from app.models import VoiceSummary

router = APIRouter(prefix="/api", tags=["clone"])

MAX_RATE = 48_000


@router.post("/voices/clone", response_model=VoiceSummary)
async def clone(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(...),
    language: str = Form(...),
    ref_text: str = Form(""),
    consent: str = Form(""),
) -> dict:
    settings: Settings = request.app.state.settings
    if consent.strip().lower() not in ("true", "on"):
        raise BadRequest(
            "Consent is required to clone a voice.", param="consent", code="consent_required"
        )
    if not (file.filename or "").strip():
        raise BadRequest("An audio file is required.", param="file", code="file_required")

    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise BadRequest("Audio file is too large (max 20 MB).", param="file")
    try:
        samples, rate = audio.read_audio(data)
    except Exception as exc:  # noqa: BLE001 - any decode failure is invalid audio
        raise BadRequest(
            "Could not decode audio. Upload a WAV, FLAC or MP3 file.",
            param="file",
            code="invalid_audio",
        ) from exc

    length = audio.duration_s(samples, rate)
    if not settings.min_ref_seconds <= length <= settings.max_ref_seconds:
        raise BadRequest(
            f"Reference audio must be {settings.min_ref_seconds:.0f}-"
            f"{settings.max_ref_seconds:.0f} seconds (got {length:.1f} s).",
            param="file",
            code="invalid_duration",
        )

    samples = audio.peak_normalize(audio.trim_silence(samples, rate), peak_db=-1.0)
    if rate > MAX_RATE:
        samples = audio.resample(samples, rate, MAX_RATE)
        rate = MAX_RATE

    voice = request.app.state.store.create(
        name,
        samples,
        rate,
        ref_text=ref_text,
        language=language,
        consent="uploaded-via-studio",
        source="uploaded",
        metadata={"original_filename": file.filename or "", "mime_type": "audio/wav"},
    )
    return voice.summary()


@router.post("/transcribe")
async def transcribe(request: Request, file: UploadFile = File(...)) -> dict[str, str]:
    settings: Settings = request.app.state.settings
    if not settings.stt_url:
        raise APIError(503, "Transcription is not configured", code="stt_not_configured")
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise BadRequest("Audio file is too large (max 20 MB).", param="file")
    url = f"{settings.stt_url.rstrip('/')}/audio/transcriptions"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                url,
                files={"file": (file.filename or "audio.wav", data, file.content_type)},
                data={"model": "whisper-1"},
            )
    except httpx.HTTPError as exc:
        raise APIError(
            502, f"Transcription request failed: {exc}", type="upstream_error", code="stt_error"
        ) from exc
    if response.status_code >= 400:
        raise APIError(
            502,
            f"Transcription upstream returned {response.status_code}",
            type="upstream_error",
            code="stt_error",
        )
    return {"text": str(response.json().get("text", ""))}
