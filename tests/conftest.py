"""Shared fixtures: temp data dir, fake engine, voice library, app client."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
from app.config import Settings
from app.main import create_app
from app.store import VoiceStore
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.fake_engine import SAMPLE_RATE, FakeEngine


@pytest.fixture(scope="session")
def fake_engine() -> FakeEngine:
    engine = FakeEngine()
    engine.start()
    yield engine
    engine.stop()


@pytest.fixture(autouse=True)
def _reset_fake_engine(fake_engine: FakeEngine) -> None:
    fake_engine.reset()


@pytest.fixture
def settings(tmp_path: Path, fake_engine: FakeEngine) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        engine_url=fake_engine.url,
        static_dir=Path(__file__).resolve().parents[1] / "app" / "static",
        asset_dir=Path(__file__).resolve().parents[1] / "app" / "assets",
        api_keys="",
        studio_auth="",
        take_retention=50,
        startup_page=False,
    )


@pytest.fixture
def store(settings: Settings) -> VoiceStore:
    return VoiceStore(settings)


@pytest.fixture
def make_voice(store: VoiceStore):
    def _make(name: str = "ashley-en", language: str = "en", seconds: float = 1.0):
        t = np.linspace(0, seconds, int(24000 * seconds), endpoint=False)
        return store.create(
            name,
            (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32),
            24000,
            ref_text="Thanks for waiting. Everything checks out.",
            language=language,
            consent="uploaded-via-studio",
        )

    return _make


@pytest.fixture
def library(store: VoiceStore, make_voice):
    make_voice("ashley-en", "en")
    make_voice("ashley-ja", "ja")
    return store


@pytest.fixture
def client(settings: Settings, fake_engine: FakeEngine):
    app = create_app(settings)
    with TestClient(app) as test_client:
        _wait_for_warm(test_client, app)
        yield test_client


def _wait_for_warm(test_client: TestClient, app: FastAPI, timeout: float = 5.0) -> None:
    """Warm-only routing (M1-T18): ``/v1`` and ``/api/speak`` are 503 until ``/ready`` is 200.

    An intentionally dead engine never warms, so stop waiting as soon as its health is
    known-bad instead of burning the whole timeout on the not-ready path.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if test_client.get("/ready").status_code == 200:
            return
        if not getattr(app.state.engine, "ready", False):
            return
        time.sleep(0.05)


__all__ = ["SAMPLE_RATE"]
