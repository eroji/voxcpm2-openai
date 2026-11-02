"""Shared fixtures: temp data dir, fake engine, voice library, app client."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from app.config import Settings
from app.main import create_app
from app.store import VoiceStore
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
        import_dir=tmp_path / "import",
        engine_url=fake_engine.url,
        static_dir=Path(__file__).resolve().parents[1] / "app" / "static",
        asset_dir=Path(__file__).resolve().parents[1] / "app" / "assets",
        api_keys="",
        studio_auth="",
        take_retention=50,
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
        yield test_client


__all__ = ["SAMPLE_RATE"]
