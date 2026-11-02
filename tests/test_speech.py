"""SpeechService regressions from the wave-2 review (M1-T7):

- slot released on mid-stream cancel and on ffmpeg spawn failure,
- ``StreamHandle.aclose`` releases even when the upstream close is cancelled,
- unknown voice → 400 listing valid names (SPEC §6),
- streaming treats 48 kHz as the engine-native input rate regardless of
  ``SAMPLE_RATE``,
- ``first_audio_ms`` includes the engine's pre-header latency.
"""

from __future__ import annotations

import asyncio
import contextlib
import struct
from typing import Any

import pytest
from app.audio import read_audio
from app.config import Settings
from app.engine import EngineClient, SynthParams
from app.errors import BadRequest, EngineError, EngineNotReady, QueueFull
from app.models import SpeechRequest
from app.speech import SpeechService
from app.store import VoiceStore

from tests.test_engine import _closed_port_url


def _service(store: VoiceStore, settings: Settings, url: str) -> tuple[SpeechService, EngineClient]:
    engine = EngineClient(url, concurrency=1, max_queue=0)
    return SpeechService(store, engine, settings), engine


def _req(fmt: str = "wav", *, input: str = "hello there", **kw: Any) -> SpeechRequest:
    return SpeechRequest(input=input, voice="ashley-en", response_format=fmt, **kw)


@pytest.mark.parametrize("fmt", ["pcm", "wav", "mp3"])
async def test_stream_completes_and_releases_slot(
    fake_engine: Any, settings: Settings, library: VoiceStore, fmt: str
) -> None:
    service, engine = _service(library, settings, fake_engine.url)
    await engine.start()
    speech = await service.stream(_req(fmt, stream=True))
    data = b"".join([chunk async for chunk in speech.chunks])
    if fmt == "wav":
        assert data[:4] == b"RIFF"
        assert struct.unpack("<I", data[24:28])[0] == 48000  # engine-native rate
    assert data
    result = await service.synthesize(_req())  # slot is free again
    assert result.audio
    await engine.aclose()


async def test_stream_queue_full_raised_before_chunks(
    fake_engine: Any, settings: Settings, library: VoiceStore
) -> None:
    service, engine = _service(library, settings, fake_engine.url)
    await engine.start()
    held = await service.stream(_req("pcm", stream=True))
    with pytest.raises(QueueFull):
        await service.stream(_req("pcm", stream=True))
    async for _ in held.chunks:
        pass
    await engine.aclose()


async def test_stream_dead_engine_not_ready_before_chunks(
    settings: Settings, library: VoiceStore
) -> None:
    service, engine = _service(library, settings, _closed_port_url())
    with pytest.raises(EngineNotReady):
        await service.stream(_req("pcm", stream=True))
    await engine.aclose()


async def test_unknown_voice_is_bad_request_with_names(
    fake_engine: Any, settings: Settings, library: VoiceStore
) -> None:
    service, engine = _service(library, settings, fake_engine.url)
    with pytest.raises(BadRequest) as exc:
        service.resolve(SpeechRequest(input="hi", voice="Nope"))
    assert exc.value.status_code == 400
    assert exc.value.param == "voice"
    assert "ashley-en" in exc.value.message
    await engine.aclose()


async def test_ffmpeg_spawn_failure_releases_slot(
    fake_engine: Any, settings: Settings, library: VoiceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    service, engine = _service(library, settings, fake_engine.url)
    await engine.start()
    with pytest.raises(EngineError):
        await service.stream(_req("mp3", stream=True))
    monkeypatch.undo()
    result = await service.synthesize(_req())  # slot was released despite the failed spawn
    assert result.audio
    await engine.aclose()


async def test_cancel_mid_stream_releases_slot(
    fake_engine: Any, settings: Settings, library: VoiceStore
) -> None:
    service, engine = _service(library, settings, fake_engine.url)
    await engine.start()
    speech = await service.stream(_req("pcm", stream=True))
    assert await speech.chunks.__anext__()  # stream is live, slot taken
    await speech.chunks.aclose()  # client disconnect mid-stream
    result = await service.synthesize(_req())
    assert result.audio
    await engine.aclose()


async def test_first_audio_ms_includes_pre_header_latency(
    fake_engine: Any, settings: Settings, library: VoiceStore
) -> None:
    service, engine = _service(library, settings, fake_engine.url)
    await engine.start()
    speech = await service.stream(_req("pcm", stream=True, input="slow and steady"))
    async for _ in speech.chunks:
        pass
    first = engine.stats().first_audio_ms
    assert first is not None and first >= 100  # fake engine sleeps 250 ms before headers
    await engine.aclose()


async def test_aclose_cancellation_releases_slot(
    fake_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _BoomResponse:
        async def aiter_bytes(self) -> Any:
            for _ in range(3):
                yield b"\x01\x00"

        async def aclose(self) -> None:
            raise asyncio.CancelledError

    engine = EngineClient(fake_engine.url, concurrency=1, max_queue=0)
    await engine.start()

    async def fake_post(params: SynthParams, *, stream: bool) -> Any:
        return _BoomResponse()

    monkeypatch.setattr(engine, "_post", fake_post)
    handle = await engine.open_stream(SynthParams(text="hi", fmt="pcm", stream=True))
    assert await handle.chunks.__anext__()
    with pytest.raises(asyncio.CancelledError):
        await handle.aclose()
    assert engine._sem._value == 1  # slot released even though resp.aclose was cancelled
    with contextlib.suppress(BaseException):
        await handle.chunks.aclose()
    await engine.aclose()


@pytest.mark.parametrize("fmt", ["pcm", "wav"])
async def test_stream_input_rate_is_engine_native_not_sample_rate(
    fake_engine: Any, settings: Settings, library: VoiceStore, fmt: str
) -> None:
    settings = settings.model_copy(update={"sample_rate": 24000})
    service, engine = _service(library, settings, fake_engine.url)
    await engine.start()
    speech = await service.stream(_req(fmt, stream=True))
    assert speech.sample_rate == 24000
    data = b"".join([chunk async for chunk in speech.chunks])
    if fmt == "pcm":
        seconds = len(data) / 2 / 24000  # raw s16le at the requested output rate
    else:
        samples, rate = read_audio(data)
        seconds = len(samples) / rate
    assert abs(seconds - 0.6) < 0.1  # engine streamed 0.6 s at 48 kHz, not mislabelled 24 kHz
    await engine.aclose()
