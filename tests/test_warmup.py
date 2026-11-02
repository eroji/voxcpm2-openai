"""Warm-up behaviour and /ready gating (M1-T4)."""

from __future__ import annotations

import socket
import time
from typing import Any

from app.config import Settings
from app.engine import EngineClient
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
