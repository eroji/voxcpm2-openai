"""EngineClient tests: health, synth, stream, queue, metrics, cfg/steps retry (M1-T1)."""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import threading
import time
from typing import Any

import numpy as np
import pytest
import uvicorn
from app.audio import duration_s, pcm_s16le_to_float, read_audio, wav_bytes
from app.engine import EngineClient, SynthParams, SynthResult, _wav_header
from app.errors import EngineError, EngineNotReady, QueueFull
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from tests.fake_engine import SAMPLE_RATE, read_wav_duration


def _closed_port_url() -> str:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}"


def _serve(app: FastAPI) -> tuple[str, uvicorn.Server, threading.Thread]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return f"http://127.0.0.1:{port}", server, thread
        except OSError:
            time.sleep(0.02)
    raise RuntimeError("test server did not start")


async def test_health(fake_engine: Any) -> None:
    engine = EngineClient(fake_engine.url)
    await engine.start()
    assert engine.ready is True
    assert await engine.health() is True
    await engine.aclose()

    dead = EngineClient(_closed_port_url())
    await dead.start()  # must not raise even though the engine is down
    assert await dead.health() is False
    assert dead.ready is False
    await dead.aclose()


async def test_synth(fake_engine: Any) -> None:
    engine = EngineClient(fake_engine.url)
    await engine.start()
    result = await engine.synth(SynthParams(text="hello there", ref_audio="data:,", ref_text="r"))
    assert isinstance(result, SynthResult)
    assert abs(read_wav_duration(result.audio) - 0.6) < 0.05
    samples, rate = read_audio(result.audio)
    assert rate == SAMPLE_RATE
    assert isinstance(result.rtf, float) and result.rtf > 0
    assert result.first_audio_ms is None  # streaming-only metric (SPEC §4)
    stats = engine.stats()
    assert stats.rtf == pytest.approx(result.rtf)
    assert stats.first_audio_ms is None
    await engine.aclose()


async def test_stream(fake_engine: Any) -> None:
    engine = EngineClient(fake_engine.url)
    await engine.start()
    handle = await engine.open_stream(
        SynthParams(text="hi", ref_audio="data:,", fmt="pcm", stream=True)
    )
    chunks = [chunk async for chunk in handle.chunks]
    await handle.aclose()  # idempotent after exhaustion
    assert len(chunks) >= 2
    assert fake_engine.state["last"]["stream_format"] == "audio"
    pcm = b"".join(chunks)
    assert len(pcm) % 2 == 0
    assert b"RIFF" not in pcm  # the container the engine streamed is stripped (M1-T20)
    assert handle.sample_rate == SAMPLE_RATE
    assert abs(duration_s(pcm_s16le_to_float(pcm), SAMPLE_RATE) - 0.6) < 0.05
    assert engine.stats().first_audio_ms is not None
    await engine.aclose()


async def test_stream_asks_the_engine_for_a_self_describing_container(fake_engine: Any) -> None:
    """`fmt="pcm"` stays a *downstream* hint; the engine is asked for wav (M1-T20)."""
    engine = EngineClient(fake_engine.url)
    await engine.start()
    handle = await engine.open_stream(
        SynthParams(text="hi", ref_audio="data:,", fmt="pcm", stream=True)
    )
    assert fake_engine.state["last"]["response_format"] == "wav"
    assert fake_engine.state["last"]["stream"] is True
    await handle.aclose()
    await engine.aclose()


async def test_stream_float32_is_converted_to_s16le(fake_engine: Any) -> None:
    """A float32 engine stream must not reach the browser as noise (reported bug)."""
    fake_engine.state["stream_format"] = "wav_f32"
    engine = EngineClient(fake_engine.url)
    await engine.start()
    handle = await engine.open_stream(
        SynthParams(text="hi", ref_audio="data:,", fmt="pcm", stream=True)
    )
    pcm = b"".join([chunk async for chunk in handle.chunks])
    await handle.aclose()
    assert handle.sample_rate == SAMPLE_RATE
    samples = pcm_s16le_to_float(pcm)
    assert abs(duration_s(samples, SAMPLE_RATE) - 0.6) < 0.05
    assert np.isfinite(samples).all()
    assert 0.0 < float(np.max(np.abs(samples))) <= 1.0  # real audio, no static
    await engine.aclose()


async def test_stream_reports_the_engine_rate(fake_engine: Any) -> None:
    fake_engine.state["stream_rate"] = 24000
    engine = EngineClient(fake_engine.url)
    await engine.start()
    handle = await engine.open_stream(
        SynthParams(text="hi", ref_audio="data:,", fmt="pcm", stream=True)
    )
    pcm = b"".join([chunk async for chunk in handle.chunks])
    await handle.aclose()
    assert handle.sample_rate == 24000
    assert abs(duration_s(pcm_s16le_to_float(pcm), 24000) - 0.6) < 0.05
    await engine.aclose()


async def test_stream_without_a_header_falls_back_to_engine_defaults(fake_engine: Any) -> None:
    """SPEC §6 assumption: an engine that ignores response_format=wav is s16le @ 48 kHz."""
    fake_engine.state["stream_format"] = "pcm16"
    engine = EngineClient(fake_engine.url)
    await engine.start()
    handle = await engine.open_stream(
        SynthParams(text="hi", ref_audio="data:,", fmt="pcm", stream=True)
    )
    assert handle.sample_rate == SAMPLE_RATE
    pcm = b"".join([chunk async for chunk in handle.chunks])
    await handle.aclose()
    assert abs(duration_s(pcm_s16le_to_float(pcm), SAMPLE_RATE) - 0.6) < 0.05
    await engine.aclose()


def test_wav_header_parser_walks_chunks() -> None:
    """The parser reads fmt/data wherever they sit, not just at the fixed 44-byte offsets."""
    from tests.fake_engine import _stream_header

    plain = _stream_header(24000, 3, 32)
    assert _wav_header(plain) == (44, 3, 1, 24000, 32)
    padded = plain[:12] + b"LIST" + struct.pack("<I", 6) + b"info\x00\x00" + plain[12:]
    assert _wav_header(padded) == (58, 3, 1, 24000, 32)
    assert _wav_header(plain[:30]) is None  # needs more bytes
    assert _wav_header(b"RIFF\x00\x00\x00\x00WAVE") is None


async def test_stream_unsupported_sample_format() -> None:
    """24-bit engine audio is a 502, never noise sent to the browser."""
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/audio/speech")
    async def speech() -> StreamingResponse:
        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF",
            0xFFFFFFFF,
            b"WAVE",
            b"fmt ",
            16,
            1,
            1,
            SAMPLE_RATE,
            SAMPLE_RATE * 3,
            3,
            24,
            b"data",
            0xFFFFFFFF,
        )

        async def gen() -> Any:
            yield header + b"\x00" * 300

        return StreamingResponse(gen(), media_type="audio/wav")

    url, server, thread = _serve(app)
    try:
        engine = EngineClient(url)
        await engine.start()
        with pytest.raises(EngineError, match="unsupported engine stream format"):
            await engine.open_stream(SynthParams(text="hi", fmt="pcm", stream=True))
        assert engine._sem._value == engine.concurrency  # the failed header read freed the slot
        await engine.aclose()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


async def test_record_false_keeps_the_metrics_window_empty(fake_engine: Any) -> None:
    """synth/open_stream(record=False) must not touch the RTF / first-audio deques."""
    engine = EngineClient(fake_engine.url)
    await engine.start()
    await engine.synth(SynthParams(text="warm-up"), record=False)
    handle = await engine.open_stream(
        SynthParams(text="warm-up", fmt="pcm", stream=True), record=False
    )
    assert [chunk async for chunk in handle.chunks]
    await handle.aclose()
    stats = engine.stats()
    assert stats.rtf is None
    assert stats.first_audio_ms is None

    await engine.synth(SynthParams(text="a real request"))  # the default still records
    assert engine.stats().rtf is not None
    await engine.aclose()


async def test_queue_full(fake_engine: Any) -> None:
    engine = EngineClient(fake_engine.url, concurrency=1, max_queue=0)
    await engine.start()
    results = await asyncio.gather(
        *(engine.synth(SynthParams(text="slow please")) for _ in range(2)),
        return_exceptions=True,
    )
    assert sum(isinstance(r, QueueFull) for r in results) >= 1
    assert sum(isinstance(r, SynthResult) for r in results) >= 1
    assert engine.stats().queue_depth == 0
    assert engine.stats().max_concurrency == 1
    await engine.aclose()


async def test_stats_without_nvidia_smi(fake_engine: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr("app.engine.subprocess.run", boom)
    engine = EngineClient(fake_engine.url, concurrency=3, max_queue=5)
    await engine.start()
    stats = engine.stats()
    assert stats.gpu is None
    assert stats.vram_used_mb is None
    assert stats.vram_total_mb is None
    assert stats.max_concurrency == 3
    assert stats.queue_depth == 0
    assert stats.rtf is None and stats.first_audio_ms is None
    assert stats.ready is True
    await engine.synth(SynthParams(text="x"))
    assert engine.stats().rtf is not None and engine.stats().rtf > 0
    await engine.aclose()


def _rejection_app(
    calls: list[dict[str, Any]], *, field: str = "steps", status: int = 422
) -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/audio/speech")
    async def speech(request: Request) -> Response:
        body = await request.json()
        calls.append(body)
        if field in body or (field == "steps" and "cfg" in body):
            return JSONResponse(
                {"error": {"message": f"unknown field: {field}"}}, status_code=status
            )
        t = np.linspace(0, 0.6, int(SAMPLE_RATE * 0.6), endpoint=False)
        tone = (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        return Response(wav_bytes(tone, SAMPLE_RATE), media_type="audio/wav")

    return app


async def test_cfg_steps_rejected(caplog: pytest.LogCaptureFixture) -> None:
    calls: list[dict[str, Any]] = []
    url, server, thread = _serve(_rejection_app(calls))
    try:
        engine = EngineClient(url)
        await engine.start()
        with caplog.at_level(logging.WARNING, logger="app.engine"):
            result = await engine.synth(SynthParams(text="hi", cfg=2.0, steps=4))
        assert isinstance(result, SynthResult)
        assert len(calls) == 2  # one 422, then exactly one retry
        assert "steps" in calls[0] and "cfg" in calls[0]
        assert "steps" not in calls[1] and "cfg" not in calls[1]
        warned = [r for r in caplog.records if "cfg/steps" in r.getMessage()]
        assert len(warned) == 1
        # later calls drop cfg/steps up front: no extra round trip, no second warning
        await engine.synth(SynthParams(text="hi", cfg=2.0, steps=4))
        assert len(calls) == 3
        assert "steps" not in calls[2]
        assert len([r for r in caplog.records if "cfg/steps" in r.getMessage()]) == 1
        await engine.aclose()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


async def test_instructions_rejected(caplog: pytest.LogCaptureFixture) -> None:
    """S1 fallback: engine rejecting `instructions` → accept, retry once, log once."""
    calls: list[dict[str, Any]] = []
    url, server, thread = _serve(_rejection_app(calls, field="instructions", status=400))
    try:
        engine = EngineClient(url)
        await engine.start()
        with caplog.at_level(logging.WARNING, logger="app.engine"):
            result = await engine.synth(SynthParams(text="hi", instructions="warm female voice"))
        assert isinstance(result, SynthResult)
        assert len(calls) == 2  # one 400, then exactly one retry
        assert "instructions" in calls[0] and "instructions" not in calls[1]
        assert len([r for r in caplog.records if "instructions" in r.getMessage()]) == 1
        # later calls drop instructions up front: no extra round trip, no second warning
        await engine.synth(SynthParams(text="hi", instructions="warm"))
        assert len(calls) == 3 and "instructions" not in calls[2]
        assert len([r for r in caplog.records if "instructions" in r.getMessage()]) == 1
        await engine.aclose()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _status_app(status: int, *, retry_after: str | None = None) -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/audio/speech")
    async def speech() -> Response:
        headers = {"Retry-After": retry_after} if retry_after else {}
        return JSONResponse(
            {"error": {"message": "engine is busy"}}, status_code=status, headers=headers
        )

    return app


async def test_upstream_429_maps_to_queue_full() -> None:
    url, server, thread = _serve(_status_app(429, retry_after="7"))
    try:
        engine = EngineClient(url)
        await engine.start()
        with pytest.raises(QueueFull) as excinfo:
            await engine.synth(SynthParams(text="hi"))
        assert excinfo.value.headers["Retry-After"] == "7"
        await engine.aclose()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


async def test_upstream_503_maps_to_engine_not_ready() -> None:
    url, server, thread = _serve(_status_app(503))
    try:
        engine = EngineClient(url)
        await engine.start()
        with pytest.raises(EngineNotReady) as excinfo:
            await engine.synth(SynthParams(text="hi"))
        assert excinfo.value.headers["Retry-After"] == "5"
        await engine.aclose()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
