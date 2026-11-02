"""Voice resolution + synthesis orchestration shared by ``/v1`` and ``/api``."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass

from app import audio as audio_utils
from app.config import Settings
from app.engine import EngineClient, StreamHandle, SynthParams
from app.errors import BadRequest, EngineError
from app.models import SpeechRequest
from app.store import VoiceStore

logger = logging.getLogger(__name__)
_speed_stream_logged = False


@dataclass(slots=True)
class ResolvedVoice:
    name: str
    ref_audio: str
    ref_text: str
    language: str


@dataclass(slots=True)
class SpeechResult:
    audio: bytes
    media_type: str
    sample_rate: int
    duration_s: float
    rtf: float | None
    first_audio_ms: float | None
    voice: str


@dataclass(slots=True)
class StreamingSpeech:
    voice: str
    media_type: str
    sample_rate: int
    chunks: AsyncIterator[bytes]


def wav_stream_header(sample_rate: int) -> bytes:
    """44-byte PCM16 mono WAV header with unknown (0xFFFFFFFF) sizes for streaming."""
    data_size = 0xFFFFFFFF
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        0xFFFFFFFF,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        sample_rate * 2,
        2,
        16,
        b"data",
        data_size,
    )


def _ffmpeg_args(fmt: str, sample_rate_in: int, sample_rate_out: int) -> list[str]:
    args = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "s16le",
        "-ar",
        str(sample_rate_in),
        "-ac",
        "1",
        "-i",
        "pipe:0",
    ]
    if sample_rate_out != sample_rate_in:
        args += ["-ar", str(sample_rate_out)]
    if fmt == "wav":
        args += ["-f", "wav"]
    elif fmt == "pcm":
        args += ["-f", "s16le"]
    elif fmt == "mp3":
        args += ["-c:a", "libmp3lame", "-b:a", "128k", "-f", "mp3"]
    elif fmt == "opus":
        args += ["-c:a", "libopus", "-b:a", "64k", "-f", "ogg"]
    elif fmt == "flac":
        args += ["-c:a", "flac", "-f", "flac"]
    else:
        raise ValueError(f"unsupported streaming format: {fmt}")
    args += ["-ac", "1", "pipe:1"]
    return args


async def ffmpeg_stream(
    chunks: AsyncGenerator[bytes, None], sample_rate_in: int, sample_rate_out: int, fmt: str
) -> AsyncIterator[bytes]:
    proc = await asyncio.create_subprocess_exec(
        *_ffmpeg_args(fmt, sample_rate_in, sample_rate_out),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdin, stdout, stderr_pipe = proc.stdin, proc.stdout, proc.stderr
    if stdin is None or stdout is None or stderr_pipe is None:
        raise EngineError("ffmpeg pipes unavailable")

    async def feed() -> None:
        try:
            async for chunk in chunks:
                stdin.write(chunk)
                await stdin.drain()
        except Exception as exc:  # noqa: BLE001 - propagate to the reader below
            proc.kill()
            raise exc
        finally:
            if not stdin.is_closing():
                stdin.close()

    feeder = asyncio.create_task(feed())
    try:
        while True:
            out = await stdout.read(8192)
            if not out:
                break
            yield out
        # Normal end of stream: stdout hit EOF, so ffmpeg is finishing; safe to join.
        feed_result = (await asyncio.gather(feeder, return_exceptions=True))[0]
        if proc.returncode is None:
            await proc.wait()
        stderr = await stderr_pipe.read()
        if isinstance(feed_result, Exception):
            raise feed_result
        if proc.returncode != 0:
            raise EngineError(f"ffmpeg stream failed: {stderr.decode('utf-8', 'replace')[:500]}")
    finally:
        # Client disconnect / cancel: never `await feeder` while stdout is unread —
        # ffmpeg would block on a full stdout pipe and the engine slot would leak.
        if not feeder.done():
            feeder.cancel()
        with contextlib.suppress(BaseException):
            await asyncio.gather(feeder, return_exceptions=True)
        if proc.returncode is None:
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        with contextlib.suppress(Exception):
            await stderr_pipe.read()
        with contextlib.suppress(Exception):
            await chunks.aclose()


class SpeechService:
    def __init__(self, store: VoiceStore, engine: EngineClient, settings: Settings) -> None:
        self.store = store
        self.engine = engine
        self.settings = settings

    def _resolve_adhoc(self, request: SpeechRequest) -> ResolvedVoice:
        ref_audio = (request.ref_audio or "").strip()
        if audio_utils.is_data_uri(ref_audio):
            try:
                audio_utils.decode_data_uri(ref_audio)
            except Exception as exc:  # noqa: BLE001
                raise BadRequest(
                    "ref_audio is not a decodable base64 audio data URI.",
                    param="ref_audio",
                    code="invalid_ref_audio",
                ) from exc
        elif audio_utils.is_http_url(ref_audio):
            if not self.settings.allow_remote_ref_audio:
                raise BadRequest(
                    "Remote ref_audio URLs are disabled. Use a data URI.",
                    param="ref_audio",
                    code="remote_ref_audio_disabled",
                )
        else:
            raise BadRequest(
                "ref_audio must be a data URI or an http(s) URL.",
                param="ref_audio",
                code="invalid_ref_audio",
            )
        return ResolvedVoice(
            name=request.voice or "ad-hoc",
            ref_audio=ref_audio,
            ref_text=request.ref_text or "",
            language="",
        )

    def resolve(self, request: SpeechRequest) -> ResolvedVoice:
        if request.ref_audio:
            return self._resolve_adhoc(request)
        voice = self.store.get(request.voice)
        return ResolvedVoice(
            name=voice.name,
            ref_audio=self.store.data_uri(voice.key),
            ref_text=voice.ref_text,
            language=voice.language,
        )

    def _params(self, request: SpeechRequest, voice: ResolvedVoice, *, stream: bool) -> SynthParams:
        return SynthParams(
            text=request.input,
            ref_audio=voice.ref_audio,
            ref_text=voice.ref_text,
            instructions=request.instructions,
            # Native speed control is unverified (S4); applying ffmpeg atempo locally avoids
            # double-processing if the engine ever supports it.
            speed=None,
            seed=request.seed,
            cfg=request.cfg,
            steps=request.steps,
            fmt="pcm" if stream else "wav",
            stream=stream,
        )

    async def synthesize(self, request: SpeechRequest) -> SpeechResult:
        voice = self.resolve(request)
        result = await self.engine.synth(self._params(request, voice, stream=False))
        samples, sample_rate = audio_utils.read_audio(result.audio)
        target_rate = request.sample_rate or self.settings.sample_rate
        encoded = audio_utils.transcode(
            samples,
            sample_rate,
            request.response_format,
            target_rate=target_rate,
            speed=request.speed,
        )
        duration = audio_utils.duration_s(samples, sample_rate) / max(request.speed, 0.01)
        return SpeechResult(
            audio=encoded,
            media_type=audio_utils.mime_for(request.response_format),
            sample_rate=target_rate,
            duration_s=duration,
            rtf=result.rtf,
            first_audio_ms=result.first_audio_ms,
            voice=voice.name,
        )

    async def stream(self, request: SpeechRequest) -> StreamingSpeech:
        global _speed_stream_logged
        if request.speed != 1.0 and not _speed_stream_logged:
            logger.warning("speed is ignored for streamed responses (SPEC §6)")
            _speed_stream_logged = True

        voice = self.resolve(request)
        target_rate = request.sample_rate or self.settings.sample_rate
        handle = await self.engine.open_stream(self._params(request, voice, stream=True))
        source = self._hold(handle)
        fmt = request.response_format

        if fmt == "pcm" and target_rate == self.settings.sample_rate:
            chunks: AsyncIterator[bytes] = source
        elif fmt == "wav" and target_rate == self.settings.sample_rate:
            chunks = self._wav_native(source, target_rate)
        else:
            chunks = ffmpeg_stream(source, self.settings.sample_rate, target_rate, fmt)

        return StreamingSpeech(
            voice=voice.name,
            media_type=audio_utils.mime_for(fmt),
            sample_rate=target_rate,
            chunks=chunks,
        )

    @staticmethod
    async def _hold(handle: StreamHandle) -> AsyncGenerator[bytes, None]:
        """Pass chunks through, guaranteeing the engine slot is released on any exit."""
        try:
            async for chunk in handle.chunks:
                yield chunk
        finally:
            await handle.aclose()

    async def _wav_native(
        self, source: AsyncGenerator[bytes, None], sample_rate: int
    ) -> AsyncIterator[bytes]:
        yield wav_stream_header(sample_rate)
        try:
            async for chunk in source:
                yield chunk
        finally:
            await source.aclose()
