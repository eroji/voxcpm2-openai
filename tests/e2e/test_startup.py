"""Live-browser startup page (M2-T15b, M3-T13): starting view, failed view, ready redirect.

The failed-state app points at a dead engine with a short failure deadline, so the page must
show the four steps *before* that deadline and then flip to the failed view. The ready-path app
uses a FakeEngine whose ``/health`` stays 503 until the test opens the gate, so the starting page
is definitely served first and the ``location.replace('/')`` after warm-up is the only navigation.
Screenshots: both startup states at 1280 and 390, plus the Studio after the redirect.
"""

from __future__ import annotations

import contextlib
import re
import threading

import httpx
import pytest
from fastapi.responses import Response
from playwright.sync_api import expect
from tests.fake_engine import FakeEngine

from conftest import RESULTS_DIR, _serve, _settings

DEAD_ENGINE = "http://127.0.0.1:9"
VIEWPORTS = {1280: {"width": 1280, "height": 900}, 390: {"width": 390, "height": 844}}
_serve_app = contextlib.contextmanager(_serve)  # conftest's _serve is a bare generator


class _GatedEngine(FakeEngine):
    """FakeEngine that holds ``/health`` at 503 until ``gate`` is set: the model-load window."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()

        @self.app.middleware("http")
        async def hold(request, call_next):  # noqa: ANN001 - test-only ASGI shim
            if request.url.path == "/health" and not self.gate.is_set():
                return Response(status_code=503)
            return await call_next(request)


@pytest.mark.parametrize("width", [1280, 390])
def test_startup_page_then_failed_state(page, fake_engine, tmp_path, width) -> None:
    settings = _settings(
        tmp_path / "data",
        fake_engine,
        engine_url=DEAD_ENGINE,
        startup_page=True,
        startup_fail_after_s=4,
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    page.set_viewport_size(VIEWPORTS[width])
    with _serve_app(settings) as base:
        page.goto(f"{base}/")
        expect(page.locator("#h")).to_have_text("Starting the voice engine", timeout=4000)
        expect(page.locator("#steps .step")).to_have_count(4)
        expect(page.locator("#elapsed")).to_have_text(re.compile(r"^\d{2}:\d{2}$"))
        expect(page.locator("#log-box")).to_have_attribute("tabindex", "0")
        expect(page.locator("#log-box")).to_have_attribute("role", "region")
        expect(page.locator("#startup-error")).to_be_hidden()
        page.screenshot(path=str(RESULTS_DIR / f"ui-starting-{width}.png"), full_page=True)

        expect(page.locator("#h")).to_have_text("The voice engine didn’t start", timeout=15000)
        expect(page.locator("#steps .step.failed")).to_have_count(1)
        expect(page.locator("#restart")).to_be_visible()
        expect(page.locator("#startup-error")).to_be_hidden()  # the API still answers: no note
        page.screenshot(path=str(RESULTS_DIR / f"ui-startup-failed-{width}.png"), full_page=True)


def test_startup_page_redirects_to_studio_once_ready(page, tmp_path) -> None:
    engine = _GatedEngine()
    engine.start()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with _serve_app(_settings(tmp_path / "data", engine, startup_page=True)) as base:
            navigations: list[str] = []
            page.on(
                "framenavigated",
                lambda frame: navigations.append(frame.url) if frame is page.main_frame else None,
            )
            page.goto(f"{base}/")
            expect(page.locator("#h")).to_have_text("Starting the voice engine", timeout=5000)
            engine.gate.set()  # weights loaded: /health answers, warm-up runs, /ready turns 200

            expect(page.locator("#screen-speak")).to_be_visible(timeout=30000)
            # the Studio page must not be cacheable: / is the same URL the page reloads itself to
            assert httpx.get(f"{base}/").headers.get("cache-control") == "no-store"
            # goto + location.replace('/') — a cached starting page would keep navigating
            assert len(navigations) <= 2, f"redirect loop: {navigations}"
            expect(page.locator("#engine-status")).to_have_text(re.compile("^Ready"), timeout=15000)
            page.screenshot(path=str(RESULTS_DIR / "ui-startup-ready-1280.png"), full_page=True)
    finally:
        engine.stop()
