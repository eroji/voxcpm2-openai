"""EngineClient tests: health, synth, stream, queue, metrics, cfg/steps retry (M1-T1)."""

from __future__ import annotations

import asyncio
import logging
import socket
import threading
import time
from typing import Any

import numpy as np
import pytest
import uvicorn
from app.audio import duration_s, pcm_s16le_to_float, read_audio, wav_bytes
from app.engine import EngineClient, SynthParams, SynthResult
from app.errors import QueueFull
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

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
    chunks = [
        chunk
        async for chunk in engine.stream(
            SynthParams(text="hi", ref_audio="data:,", fmt="pcm", stream=True)
        )
    ]
    assert len(chunks) >= 2
    pcm = b"".join(chunks)
    assert len(pcm) % 2 == 0
    assert abs(duration_s(pcm_s16le_to_float(pcm), SAMPLE_RATE) - 0.6) < 0.05
    assert engine.stats().first_audio_ms is not None
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


def _rejection_app(calls: list[dict[str, Any]]) -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/audio/speech")
    async def speech(request: Request) -> Response:
        body = await request.json()
        calls.append(body)
        if "steps" in body or "cfg" in body:
            return JSONResponse({"error": {"message": "unknown field: steps"}}, status_code=422)
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
