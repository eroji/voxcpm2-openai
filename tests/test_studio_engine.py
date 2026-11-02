"""Engine status, warm-up and restart routes (M2-T5)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from app.config import Settings
from app.errors import EngineError
from app.main import create_app
from fastapi.testclient import TestClient


def _wait_warm(client: TestClient, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.get("/ready").status_code == 200:
            return
        time.sleep(0.1)
    pytest.fail(f"never became warm: {client.get('/ready').text}")


def test_engine_status(client: TestClient, library: Any) -> None:
    _wait_warm(client)  # ready must mean warm, not mere liveness
    resp = client.get("/api/engine")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is True
    assert body["reason"] is None
    assert body["model"] == "openbmb/VoxCPM2"
    assert isinstance(body["runtime"], str) and body["runtime"]
    assert body["max_concurrency"] == 2
    assert body["voices"] == 2
    assert body["queue_depth"] >= 0
    assert body["stt"] is False  # no STT_URL
    assert body["api_keys_required"] is False  # no API_KEYS
    # GPU fields may be None on machines without nvidia-smi
    assert body["gpu"] is None or isinstance(body["gpu"], str)


def test_engine_status_flags_flip_with_envs(
    tmp_path: Path, fake_engine: Any, monkeypatch: pytest.MonkeyPatch, library: Any
) -> None:
    monkeypatch.setenv("STT_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("API_KEYS", "sk-test")
    settings = Settings(
        data_dir=tmp_path / "data",
        import_dir=tmp_path / "import",
        engine_url=fake_engine.url,
    )
    with TestClient(create_app(settings)) as client:
        body = client.get("/api/engine").json()
    assert body["stt"] is True
    assert body["api_keys_required"] is True


def test_warmup_endpoint_triggers_two_calls(client: TestClient, fake_engine: Any) -> None:
    before = fake_engine.state["calls"]
    resp = client.post("/api/engine/warmup")
    assert resp.status_code == 200
    assert resp.json() == {"warm": True}
    assert fake_engine.state["calls"] - before >= 2  # one WAV + one stream


def test_warmup_failure_returns_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    warmup = client.app.state.warmup

    async def failing_run() -> bool:
        warmup.warm = False
        warmup.last_error = "engine is not ready"
        return False

    monkeypatch.setattr(warmup, "run", failing_run)
    resp = client.post("/api/engine/warmup")
    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "5"
    assert "engine is not ready" in resp.json()["error"]["message"]


def test_restart_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def noop_restart() -> None:
        calls.append("restart")

    monkeypatch.setattr(client.app.state.engine, "restart", noop_restart)
    resp = client.post("/api/engine/restart")
    assert resp.status_code == 200
    assert resp.json() == {"restarted": True, "warm": True}
    assert calls == ["restart"]


def test_restart_waits_for_health_then_rewarms(
    client: TestClient, library: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-007/R-031: restart must not warm the old process, and /ready is 200 again after."""
    engine = client.app.state.engine
    warmup = client.app.state.warmup
    _wait_warm(client)
    real_health = engine.health
    down_until = {"t": 0.0}

    async def health() -> bool:
        if time.monotonic() < down_until["t"]:
            engine._ready = False
            return False
        return await real_health()

    async def stub_restart() -> None:
        engine._ready = False
        assert warmup.warm is False  # the router must clear warm before restarting
        down_until["t"] = time.monotonic() + 0.3
        await asyncio.sleep(0.3)  # s6-svc -r returns immediately; health still down
        await health()

    async def wait_ready(max_wait: float) -> bool:
        deadline = time.monotonic() + max_wait
        while True:
            if await health():
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.05)

    monkeypatch.setattr(engine, "health", health)
    monkeypatch.setattr(engine, "restart", stub_restart)
    monkeypatch.setattr(engine, "wait_ready", wait_ready)
    resp = client.post("/api/engine/restart")
    assert resp.status_code == 200
    assert resp.json() == {"restarted": True, "warm": True}
    assert client.get("/ready").status_code == 200


def test_ready_503_when_warm_but_engine_dead(
    client: TestClient, library: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-031: warm must never outrank engine liveness."""
    engine = client.app.state.engine
    _wait_warm(client)

    async def dead_health() -> bool:
        engine._ready = False
        return False

    monkeypatch.setattr(engine, "health", dead_health)
    engine._ready = False
    client.app.state.warmup.warm = True
    resp = client.get("/ready")
    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "5"
    assert resp.json()["error"] == "engine is not ready"


def test_restart_failure_returns_502(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def raising_restart() -> None:
        raise EngineError("s6-svc -r failed: 1")

    monkeypatch.setattr(client.app.state.engine, "restart", raising_restart)
    resp = client.post("/api/engine/restart")
    assert resp.status_code == 502
    error = resp.json()["error"]
    assert error["type"] == "upstream_error"
    assert error["code"] == "engine_error"
    assert client.app.state.warmup.warm is False
