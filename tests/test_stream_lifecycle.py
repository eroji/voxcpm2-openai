"""Streaming admission/lifecycle tests (M1-T5): 429/503 raise before headers,
cancelled or transport-broken streams release the engine slot, and a 429 from
the engine must not permanently disable cfg/steps."""

from __future__ import annotations

import asyncio
import socket
import threading
from typing import Any

import numpy as np
import pytest
from app.audio import wav_bytes
from app.engine import EngineClient, SynthParams, SynthResult
from app.errors import EngineError, EngineNotReady, QueueFull
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from tests.fake_engine import SAMPLE_RATE
from tests.test_engine import _closed_port_url, _serve


async def test_queue_full_raised_before_headers(fake_engine: Any) -> None:
    engine = EngineClient(fake_engine.url, concurrency=1, max_queue=0)
    await engine.start()
    held = await engine.open_stream(SynthParams(text="slow", fmt="pcm", stream=True))
    # The slot is held by the open handle: a second stream is refused eagerly,
    # not lazily after the router has sent 200.
    with pytest.raises(QueueFull):
        await engine.open_stream(SynthParams(text="next", fmt="pcm", stream=True))
    await held.aclose()
    assert engine.stats().queue_depth == 0
    await engine.aclose()


async def test_engine_not_ready_raised_before_headers() -> None:
    engine = EngineClient(_closed_port_url())
    with pytest.raises(EngineNotReady):
        await engine.open_stream(SynthParams(text="hi", fmt="pcm", stream=True))
    assert engine.stats().queue_depth == 0  # the failed admission released the slot
    await engine.aclose()


async def test_queue_boundary_admits_18_of_20(fake_engine: Any) -> None:
    engine = EngineClient(fake_engine.url, concurrency=2, max_queue=16)
    await engine.start()
    results = await asyncio.gather(
        *(engine.synth(SynthParams(text="slow please")) for _ in range(20)),
        return_exceptions=True,
    )
    # Q-016: 2 running + 16 queued are admitted; requests 19-20 get 429. No more.
    assert sum(isinstance(r, QueueFull) for r in results) == 2
    assert sum(isinstance(r, SynthResult) for r in results) == 18
    assert engine.stats().queue_depth == 0
    await engine.aclose()


async def test_cancelled_stream_releases_slot(fake_engine: Any) -> None:
    engine = EngineClient(fake_engine.url, concurrency=1, max_queue=0)
    await engine.start()
    handle = await engine.open_stream(SynthParams(text="hi", fmt="pcm", stream=True))
    assert await handle.chunks.__anext__()  # stream is live, slot taken
    await handle.chunks.aclose()  # client disconnect mid-stream
    result = await asyncio.wait_for(engine.synth(SynthParams(text="hi")), timeout=2.0)
    assert result.audio
    await engine.aclose()


def _truncating_server() -> tuple[str, socket.socket, threading.Thread]:
    """HTTP server that answers /health normally but cuts the speech body short."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    port = sock.getsockname()[1]

    def serve() -> None:
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            with conn:
                try:
                    request = conn.recv(65536)
                    if b"GET /health" in request:
                        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
                    else:
                        conn.sendall(
                            b"HTTP/1.1 200 OK\r\nContent-Length: 4096\r\n\r\n" + b"\x00" * 64
                        )
                except OSError:
                    pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{port}", sock, thread


async def test_transport_error_mid_body() -> None:
    url, sock, thread = _truncating_server()
    try:
        engine = EngineClient(url)
        await engine.start()
        handle = await engine.open_stream(SynthParams(text="hi", fmt="pcm", stream=True))
        with pytest.raises(EngineError):
            async for _ in handle.chunks:
                pass
        assert engine.stats().queue_depth == 0  # slot released even on failure
        await engine.aclose()
    finally:
        sock.close()
        thread.join(timeout=5)


async def test_429_does_not_disable_cfg_steps() -> None:
    calls: list[dict[str, Any]] = []
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/audio/speech")
    async def speech(request: Request) -> Response:
        body = await request.json()
        calls.append(body)
        if len(calls) == 1:
            return JSONResponse({"error": {"message": "too busy"}}, status_code=429)
        t = np.linspace(0, 0.6, int(SAMPLE_RATE * 0.6), endpoint=False)
        tone = (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        return Response(wav_bytes(tone, SAMPLE_RATE), media_type="audio/wav")

    url, server, thread = _serve(app)
    try:
        engine = EngineClient(url)
        await engine.start()
        with pytest.raises(EngineError):  # a 429 from the engine is a 502, not a schema error
            await engine.synth(SynthParams(text="hi", cfg=2.0, steps=4))
        await engine.synth(SynthParams(text="hi", cfg=2.0, steps=4))
        assert "cfg" in calls[1] and "steps" in calls[1]  # knobs were never disabled
        await engine.aclose()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


async def test_restart_missing_binary(fake_engine: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("s6-svc")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    engine = EngineClient(fake_engine.url)
    with pytest.raises(EngineError):
        await engine.restart()
    await engine.aclose()
