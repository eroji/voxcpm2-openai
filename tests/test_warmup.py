"""Warm-up behaviour and /ready gating (M1-T4)."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
from typing import Any

import pytest
from app.config import Settings
from app.engine import EngineClient, SynthParams
from app.errors import EngineError
from app.main import create_app
from app.store import VoiceStore
from app.warmup import WarmupService
from fastapi.testclient import TestClient


def _closed_port_url() -> str:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}"


async def test_run_synthesizes_and_streams(
    fake_engine: Any, library: VoiceStore, settings: Settings
) -> None:
    engine = EngineClient(fake_engine.url)
    warmup = WarmupService(library, engine, settings)
    try:
        assert await warmup.run() is True
    finally:
        await engine.aclose()
    assert fake_engine.state["calls"] >= 2  # one WAV + one stream
    assert fake_engine.state["last"]["stream"] is True  # second call was the stream
    assert warmup.warm is True
    assert warmup.last_error is None
    assert str(fake_engine.state["last"]["ref_audio"]).startswith("data:")


async def test_empty_library_uses_asset(
    fake_engine: Any, store: VoiceStore, settings: Settings
) -> None:
    assert store.list() == []
    engine = EngineClient(fake_engine.url)
    warmup = WarmupService(store, engine, settings)
    try:
        assert await warmup.run() is True
    finally:
        await engine.aclose()
    assert warmup.warm is True
    assert str(fake_engine.state["last"]["ref_audio"]).startswith("data:")
    assert fake_engine.state["last"]["ref_text"] == "Warm-up."  # Q-014


async def test_run_failure_sets_last_error(store: VoiceStore, settings: Settings) -> None:
    engine = EngineClient(_closed_port_url())
    warmup = WarmupService(store, engine, settings)
    try:
        assert await warmup.run() is False
    finally:
        await engine.aclose()
    assert warmup.warm is False
    assert warmup.last_error


def test_ready_reaches_200(settings: Settings, fake_engine: Any) -> None:
    with TestClient(create_app(settings)) as client:
        deadline = time.monotonic() + 5.0
        resp = client.get("/ready")
        while resp.status_code != 200 and time.monotonic() < deadline:
            assert client.get("/health").status_code == 200
            time.sleep(0.1)
            resp = client.get("/ready")
        assert resp.status_code == 200, resp.text
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 200  # static route smoke


def test_ready_503_before_warm(settings: Settings, fake_engine: Any, monkeypatch: Any) -> None:
    async def failing_run(self: WarmupService) -> bool:
        self.warm = False
        self.last_error = "warm-up failed"
        return False

    monkeypatch.setattr(WarmupService, "run", failing_run)
    with TestClient(create_app(settings)) as client:
        resp = client.get("/ready")
        assert resp.status_code == 503
        assert resp.headers["Retry-After"] == "5"
        assert client.get("/health").status_code == 200


async def test_stream_failure_after_wav_keeps_not_warm(
    fake_engine: Any, library: VoiceStore, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-005: WAV succeeds but the streamed request fails -> run() False, /ready stays 503."""
    engine = EngineClient(fake_engine.url)
    open_stream_calls: list[bool] = []

    async def failing_open_stream(params: SynthParams) -> Any:
        open_stream_calls.append(params.stream)
        raise EngineError("stream refused")

    monkeypatch.setattr(engine, "open_stream", failing_open_stream)
    warmup = WarmupService(library, engine, settings)
    try:
        assert await warmup.run() is False
    finally:
        await engine.aclose()
    assert warmup.warm is False
    assert warmup.last_error
    assert fake_engine.state["calls"] == 1  # the WAV synth ran first
    assert fake_engine.state["last"]["stream"] is False
    assert open_stream_calls == [True]  # second request was the stream


async def test_run_forever_not_ready_retries_until_cancelled(
    store: VoiceStore, settings: Settings, fake_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = EngineClient(fake_engine.url)

    async def never_ready(max_wait: float) -> bool:
        return False

    monkeypatch.setattr(engine, "wait_ready", never_ready)
    warmup = WarmupService(store, engine, settings)
    task = asyncio.create_task(warmup.run_forever())
    await asyncio.sleep(0.1)
    assert warmup.last_error == "engine is not ready"
    assert warmup.warm is False
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)  # exits, does not hang
    await engine.aclose()


def test_shutdown_survives_warmup_crash(
    settings: Settings, fake_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-CancelledError from run_forever must not re-raise out of lifespan."""

    async def boom(self: WarmupService) -> None:
        raise RuntimeError("warm-up exploded")

    monkeypatch.setattr(WarmupService, "run_forever", boom)
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
