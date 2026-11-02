"""E2E harness (M3-T6): real Voice Studio app + FakeEngine, driven by Playwright Chromium.

The app runs once per session on a free port with its own data dir and the three seeded
voices; every test gets a fresh browser context, so JS module state never leaks between
tests. Data the tests mutate (saved voices) is cleaned up inside the test that creates it.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import numpy as np
import pytest
import uvicorn
from app.config import Settings
from app.main import create_app
from app.store import VoiceStore
from tests.fake_engine import FakeEngine

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "test-results"
SEED_VOICES = (("ashley-en", "en"), ("ashley-ja", "ja"), ("ashley-ko", "ko"))


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_engine_ready(base_url: str, timeout: float = 30.0) -> None:
    """Block until /api/engine reports ready (covers start-up and post-restart re-warm)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{base_url}/api/engine", timeout=2.0).json().get("ready"):
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    pytest.fail(f"engine never became ready at {base_url}")


@pytest.fixture(scope="session")
def live_app(fake_engine: FakeEngine, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    data_dir = tmp_path_factory.mktemp("e2e-data")
    settings = Settings(
        data_dir=data_dir,
        import_dir=data_dir / "import",  # does not exist → the Qwen3 importer stays off
        engine_url=fake_engine.url,
        static_dir=REPO_ROOT / "app" / "static",
        asset_dir=REPO_ROOT / "app" / "assets",
        api_keys="",
        studio_auth="",
        stt_url="",
        take_retention=50,
    )
    store = VoiceStore(settings)
    t = np.linspace(0, 1.0, 24000, endpoint=False)
    for name, language in SEED_VOICES:
        store.create(
            name,
            (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32),
            24000,
            ref_text="Thanks for waiting. Everything checks out.",
            language=language,
            consent="uploaded-via-studio",
        )

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                if httpx.get(f"{base_url}/health", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.05)
        else:
            raise RuntimeError("live app did not answer /health")
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def page() -> Iterator:
    sync_api = pytest.importorskip("playwright.sync_api")
    playwright = sync_api.sync_playwright().start()
    try:
        browser = playwright.chromium.launch(headless=True)
    except sync_api.Error as exc:
        playwright.stop()
        pytest.fail(
            f"Chromium is not installed. Run: .venv/bin/playwright install chromium ({exc})"
        )
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    yield page
    context.close()
    browser.close()
    playwright.stop()


@pytest.fixture
def open_studio(live_app: str) -> Callable[..., None]:
    """Load Voice Studio (optionally deep-linked to a screen) with the engine already Ready."""

    def open_(page, screen: str = "") -> None:
        _wait_engine_ready(live_app)
        page.goto(f"{live_app}/#{screen}" if screen else f"{live_app}/")
        page.wait_for_function(
            "document.getElementById('engine-status')?.textContent?.startsWith('Ready')",
            timeout=10000,
        )

    return open_
