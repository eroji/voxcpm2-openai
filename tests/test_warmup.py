"""Warm-up behaviour and /ready gating (M1-T4)."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
from typing import Any

import pytest
from app import warmup as warmup_module
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
    # one WAV + one stream + one concurrent batch (engine_concurrency) to warm the
    # batched decode path before /ready opens
    assert fake_engine.state["calls"] >= 2 + settings.engine_concurrency
    assert fake_engine.state["max_active"] >= settings.engine_concurrency
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
    # run() waits for engine readiness first; keep that wait short, not 300 s.
    warmup = WarmupService(store, engine, settings.model_copy(update={"warmup_timeout_s": 0.5}))
    try:
        assert await warmup.run() is False
    finally:
        await engine.aclose()
    assert warmup.warm is False
    assert warmup.last_error


async def test_run_refuses_when_engine_not_ready(
    store: VoiceStore, settings: Settings, fake_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-006: run() must not synthesize against an engine that never becomes ready."""
    engine = EngineClient(fake_engine.url)

    async def never_ready(max_wait: float) -> bool:
        return False

    monkeypatch.setattr(engine, "wait_ready", never_ready)
    warmup = WarmupService(store, engine, settings)
    try:
        assert await warmup.run() is False
    finally:
        await engine.aclose()
    assert warmup.last_error == "engine is not ready"
    assert fake_engine.state["calls"] == 0


async def test_restart_waits_for_a_healthy_engine(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-007: s6-svc -r returns at once, so restart() must mark down and wait."""
    engine = EngineClient("http://127.0.0.1:1")

    class _Proc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

        def kill(self) -> None:
            pass

        async def wait(self) -> int:
            return 0

    async def fake_exec(*args: Any, **kwargs: Any) -> _Proc:
        return _Proc()

    checks: list[tuple[float, bool]] = []

    async def fake_wait_ready(max_wait: float) -> bool:
        checks.append((max_wait, engine.ready))
        return True

    engine._ready = True
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(engine, "wait_ready", fake_wait_ready)
    await engine.restart()
    assert checks == [(120, False)]  # marked down, then waited for /health
    await engine.aclose()


async def test_restart_raises_when_engine_stays_down(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = EngineClient("http://127.0.0.1:1")

    class _Proc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def fake_exec(*args: Any, **kwargs: Any) -> _Proc:
        return _Proc()

    async def never_ready(max_wait: float) -> bool:
        return False

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(engine, "wait_ready", never_ready)
    with pytest.raises(EngineError, match="did not become healthy after restart"):
        await engine.restart()
    assert engine.ready is False
    await engine.aclose()


async def test_restart_clears_the_s6_backoff_state(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A manual restart must undo the finish script's hold-down, or s6 stays down."""
    service_dir = tmp_path / "run" / "service"
    engine = EngineClient("http://127.0.0.1:1", service_dir=str(service_dir))
    calls: list[tuple[str, ...]] = []

    class _Proc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def fake_exec(*args: Any, **kwargs: Any) -> _Proc:
        calls.append(args[1:3])
        return _Proc()

    async def ready(max_wait: float) -> bool:
        return True

    down = service_dir / "engine" / "down"
    down.parent.mkdir(parents=True)
    down.touch()
    for name, body in (("engine-failures", "3"), ("engine-last-exit", "1700000000")):
        (tmp_path / "run" / name).write_text(body, encoding="utf-8")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(engine, "wait_ready", ready)
    await engine.restart()

    target = str(service_dir / "engine")
    assert calls == [("-u", target), ("-r", target)]  # up first, then restart
    assert not down.exists()
    assert not (tmp_path / "run" / "engine-failures").exists()
    assert not (tmp_path / "run" / "engine-last-exit").exists()
    await engine.aclose()


async def _until(predicate: Any, limit_s: float = 8.0) -> None:
    deadline = time.monotonic() + limit_s
    while not predicate():
        assert time.monotonic() < deadline, "condition never became true"
        await asyncio.sleep(0.05)


async def test_run_forever_clears_warm_and_rewarms_after_recovery(
    fake_engine: Any, library: VoiceStore, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-008: after an engine crash run_forever must clear warm, then re-warm by itself."""
    engine = EngineClient(fake_engine.url)
    healthy = {"up": True}

    async def fake_health() -> bool:
        engine._ready = healthy["up"]
        return engine._ready

    monkeypatch.setattr(engine, "health", fake_health)
    monkeypatch.setattr(warmup_module, "_POLL_S", 0.05)
    warmup = WarmupService(library, engine, settings)
    assert await warmup.run() is True
    assert warmup.warm is True

    healthy["up"] = False  # engine crash
    await engine.health()  # the engine's health-poll task does this in production
    task = asyncio.create_task(warmup.run_forever())
    try:
        await asyncio.wait_for(_until(lambda: warmup.warm is False, 5.0), 6.0)
        assert warmup.last_error == "engine is not ready"
        calls_before = fake_engine.state["calls"]
        healthy["up"] = True  # s6 brings the engine back
        await engine.health()
        await asyncio.wait_for(_until(lambda: warmup.warm is True, 8.0), 9.0)
        assert fake_engine.state["calls"] > calls_before  # re-warmed without a manual run()
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await engine.aclose()


class _MissEngine:
    """``ready`` reads False for the first ``down`` polls, then True again."""

    def __init__(self, down: int) -> None:
        self._down = down

    @property
    def ready(self) -> bool:
        if self._down > 0:
            self._down -= 1
            return False
        return True


async def _supervised(warmup: WarmupService, seconds: float) -> None:
    task = asyncio.create_task(warmup.run_forever())
    await asyncio.sleep(seconds)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_health_blip_does_not_clear_warm(
    store: VoiceStore, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One failed /health is a blip: warm (and with it /ready) must stay put."""
    monkeypatch.setattr(warmup_module, "_POLL_S", 0.01)
    engine = _MissEngine(2)
    warmup = WarmupService(store, engine, settings)
    warmup.warm = True
    runs: list[bool] = []

    async def fake_run(self: WarmupService) -> bool:
        runs.append(True)
        return True

    monkeypatch.setattr(WarmupService, "run", fake_run)
    await _supervised(warmup, 0.4)
    assert warmup.warm is True
    assert runs == []


async def test_three_health_misses_clear_warm_and_rewarm(
    store: VoiceStore, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(warmup_module, "_POLL_S", 0.01)
    engine = _MissEngine(3)
    warmup = WarmupService(store, engine, settings)
    warmup.warm = True

    async def fake_run(self: WarmupService) -> bool:
        warmup.warm = True
        warmup.ever_warm = True
        return True

    monkeypatch.setattr(WarmupService, "run", fake_run)
    await _supervised(warmup, 0.4)
    assert warmup.ever_warm is True  # the gate stays open through the re-warm
    assert warmup.warm is True


async def test_warm_up_leaves_the_rtf_window_alone(
    fake_engine: Any, library: VoiceStore, settings: Settings
) -> None:
    """The Engine screen's RTF / first-audio averages are real requests only."""
    engine = EngineClient(fake_engine.url)
    warmup = WarmupService(library, engine, settings)
    assert await warmup.run() is True
    stats = engine.stats()
    assert stats.rtf is None
    assert stats.first_audio_ms is None
    await engine.synth(SynthParams(text="a real request"))
    assert engine.stats().rtf is not None
    await engine.aclose()


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
        assert resp.json()["error"] == "warm-up failed"
        assert client.get("/health").status_code == 200


async def test_stream_failure_after_wav_keeps_not_warm(
    fake_engine: Any, library: VoiceStore, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-005: WAV succeeds but the streamed request fails -> run() False, /ready stays 503."""
    engine = EngineClient(fake_engine.url)
    open_stream_calls: list[bool] = []

    async def failing_open_stream(params: SynthParams, **kwargs: Any) -> Any:
        open_stream_calls.append((params.stream, kwargs.get("record")))
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
    assert open_stream_calls == [(True, False)]  # the stream, excluded from the RTF window


async def test_run_forever_not_ready_retries_until_cancelled(
    store: VoiceStore, settings: Settings, fake_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = EngineClient(fake_engine.url)

    async def down_health() -> bool:
        engine._ready = False
        return False

    monkeypatch.setattr(engine, "health", down_health)
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
