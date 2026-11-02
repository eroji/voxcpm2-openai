"""Voice resolution + synthesis orchestration shared by ``/v1`` and ``/api``."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from app import audio as audio_utils
from app.config import Settings
from app.engine import ENGINE_SAMPLE_RATE, EngineClient, StreamHandle, SynthParams
from app.errors import APIError, BadRequest, EngineError
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
    key: str | None = None
    """Lowercase ASCII lookup key; ``None`` for ad-hoc ``ref_audio`` voices."""


@dataclass(slots=True)
class SpeechResult:
    audio: bytes
    media_type: str
    sample_rate: int
    duration_s: float
    rtf: float | None
    first_audio_ms: float | None
    voice: str
    key: str | None = None


@dataclass(slots=True)
class StreamingSpeech:
    voice: str
    media_type: str
    sample_rate: int
    chunks: AsyncIterator[bytes]
    aclose: Callable[[], Awaitable[None]]
    """Releases the engine slot (and any ffmpeg process) without iterating.

    Routers must ``try/finally: await streaming.aclose()`` so a client that
    disconnects before the first chunk cannot leak a concurrency slot.
    Idempotent: safe after exhaustion and safe to call twice.
    """

    key: str | None = None
    """Lowercase ASCII lookup key; ``None`` for ad-hoc ``ref_audio`` voices."""


class _OwnedChunks:
    """Iterator that runs the owner's cleanup on exhaustion *and* on ``aclose()``.

    ``aclose()`` on a never-started async generator is a language no-op, so the
    engine slot must be released through this wrapper instead: the routers call
    ``streaming.aclose()`` and closing ``chunks`` both route to the same
    idempotent cleanup, whether or not a single chunk was ever pulled.
    """

    def __init__(
        self, source: AsyncGenerator[bytes, None], cleanup: Callable[[], Awaitable[None]]
    ) -> None:
        self._source = source
        self._cleanup = cleanup

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self

    async def __anext__(self) -> bytes:
        try:
            return await self._source.__anext__()
        except StopAsyncIteration:
            await self._cleanup()  # no-op when the generator's finally already ran
            raise

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            await self._source.aclose()
        await self._cleanup()


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
    handle: StreamHandle, sample_rate_in: int, sample_rate_out: int, fmt: str
) -> tuple[AsyncGenerator[bytes, None], Callable[[], Awaitable[None]]]:
    """Transcode the engine stream through ffmpeg.

    Spawns eagerly so a spawn/pipe failure releases the engine slot and raises
    ``EngineError`` before the router sends headers. Returns the chunk generator
    and an idempotent ``cleanup`` that terminates the process, cancels the
    feeder and releases the engine slot — the cleanup works even if the
    generator is never iterated.
    """
    try:
        args = _ffmpeg_args(fmt, sample_rate_in, sample_rate_out)
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdin, stdout, stderr_pipe = proc.stdin, proc.stdout, proc.stderr
        if stdin is None or stdout is None or stderr_pipe is None:
            raise EngineError("ffmpeg pipes unavailable")
    except BaseException as exc:  # noqa: BLE001 - ffmpeg missing, pipes refused, or cancelled
        # CancelledError lands here too (e.g. cancelled during the spawn await):
        # release the slot through the owner, then re-raise it unchanged.
        await handle.aclose()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise EngineError(f"cannot start ffmpeg: {exc}") from exc

    feeder: asyncio.Task[None] | None = None
    done = False

    async def cleanup() -> None:
        nonlocal done
        if done:
            return
        done = True
        # Client disconnect / cancel: never `await feeder` while stdout is unread —
        # ffmpeg would block on a full stdout pipe and the engine slot would leak.
        if feeder is not None and not feeder.done():
            feeder.cancel()
        if feeder is not None:
            with contextlib.suppress(BaseException):
                await asyncio.gather(feeder, return_exceptions=True)
        if not stdin.is_closing():
            stdin.close()
        if proc.returncode is None:
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        with contextlib.suppress(Exception):
            await stderr_pipe.read()
        await handle.aclose()

    async def feed() -> None:
        try:
            async for chunk in handle.chunks:
                stdin.write(chunk)
                await stdin.drain()
        except Exception as exc:  # noqa: BLE001 - propagate to the reader below
            proc.kill()
            raise exc
        finally:
            if not stdin.is_closing():
                stdin.close()

    async def pump() -> AsyncGenerator[bytes, None]:
        nonlocal feeder
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
                raise EngineError(
                    f"ffmpeg stream failed: {stderr.decode('utf-8', 'replace')[:500]}"
                )
        finally:
            await cleanup()

    return pump(), cleanup


class SpeechService:
    def __init__(self, store: VoiceStore, engine: EngineClient, settings: Settings) -> None:
        self.store = store
        self.engine = engine
        self.settings = settings

    def _resolve_adhoc(self, request: SpeechRequest) -> ResolvedVoice:
        ref_audio = (request.ref_audio or "").strip()
        # SPEC §10 upload cap spirit: bound the base64 payload *before* decoding it.
        if len(ref_audio) > self.settings.max_upload_bytes * 4 // 3 + 256:
            raise BadRequest(
                "ref_audio exceeds the maximum accepted size.",
                param="ref_audio",
                code="ref_audio_too_large",
            )
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
        try:
            voice = self.store.get(request.voice)
        except APIError as exc:
            # Any lookup failure — a display name like "Ashley EN" fails the key
            # pattern with BadRequest before the file check — is a 400 listing the
            # valid keys (SPEC §6); /api/voices/{name} keeps its 404 because it
            # uses the store directly.
            names = ", ".join(v.key for v in self.store.list())
            raise BadRequest(
                f"Unknown voice '{request.voice}'. Valid voices: {names}",
                param="voice",
                code="voice_not_found",
            ) from exc
        return ResolvedVoice(
            name=voice.name,
            ref_audio=self.store.data_uri(voice.key),
            ref_text=voice.ref_text,
            language=voice.language,
            key=voice.key,
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
        try:
            samples, sample_rate = audio_utils.read_audio(result.audio)
            target_rate = request.sample_rate or self.settings.sample_rate
            encoded = audio_utils.transcode(
                samples,
                sample_rate,
                request.response_format,
                target_rate=target_rate,
                speed=request.speed,
            )
        except EngineError:
            raise
        except Exception as exc:  # noqa: BLE001 - undecodable audio or transcode failure
            raise EngineError(f"audio conversion failed: {exc}") from exc
        duration = audio_utils.duration_s(samples, sample_rate) / max(request.speed, 0.01)
        return SpeechResult(
            audio=encoded,
            media_type=audio_utils.mime_for(request.response_format),
            sample_rate=target_rate,
            duration_s=duration,
            rtf=result.rtf,
            first_audio_ms=result.first_audio_ms,
            voice=voice.name,
            key=voice.key,
        )

    async def stream(self, request: SpeechRequest) -> StreamingSpeech:
        global _speed_stream_logged
        if request.speed != 1.0 and not _speed_stream_logged:
            logger.warning("speed is ignored for streamed responses (SPEC §6)")
            _speed_stream_logged = True

        voice = self.resolve(request)
        target_rate = request.sample_rate or self.settings.sample_rate
        handle = await self.engine.open_stream(self._params(request, voice, stream=True))
        fmt = request.response_format
        try:
            if fmt == "pcm" and target_rate == ENGINE_SAMPLE_RATE:
                source, cleanup = handle.chunks, handle.aclose
            elif fmt == "wav" and target_rate == ENGINE_SAMPLE_RATE:
                source, cleanup = self._wav_native(handle, ENGINE_SAMPLE_RATE), handle.aclose
            else:
                source, cleanup = await ffmpeg_stream(handle, ENGINE_SAMPLE_RATE, target_rate, fmt)
        except BaseException:
            # Admission succeeded but construction failed or was cancelled:
            # the slot must not depend on the generator ever starting.
            await handle.aclose()
            raise
        chunks = _OwnedChunks(source, cleanup)
        return StreamingSpeech(
            voice=voice.name,
            key=voice.key,
            media_type=audio_utils.mime_for(fmt),
            sample_rate=target_rate,
            chunks=chunks,
            aclose=chunks.aclose,
        )

    @staticmethod
    async def _wav_native(handle: StreamHandle, sample_rate: int) -> AsyncGenerator[bytes, None]:
        yield wav_stream_header(sample_rate)
        try:
            async for chunk in handle.chunks:
                yield chunk
        finally:
            await handle.aclose()
