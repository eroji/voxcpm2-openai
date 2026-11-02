"""HTTP-level proof that a full queue is a 429 + Retry-After (R-020, SPEC §6).

The engine client already raises ``QueueFull`` at admission; these tests pin the
shape all the way through the routers, including the streaming paths, which must
never send a ``200`` header before admission fails.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.errors import QueueFull
from fastapi.testclient import TestClient

BODY = {"model": "tts-1", "voice": "ashley-en", "input": "hello"}


def _full(*args: Any, **kwargs: Any) -> Any:
    async def raiser(*a: Any, **k: Any) -> None:
        raise QueueFull()

    return raiser


def _assert_429(resp: Any) -> dict:
    assert resp.status_code == 429, resp.text
    assert resp.headers["Retry-After"] == "1"
    error = resp.json()["error"]
    assert set(error) == {"message", "type", "param", "code"}
    assert error["type"] == "rate_limit_error"
    assert error["code"] == "queue_full"
    return error


def test_v1_speech_queue_full_429(
    client: TestClient, library: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client.app.state.speech, "synthesize", _full())
    _assert_429(client.post("/v1/audio/speech", json=BODY))


def test_v1_speech_stream_queue_full_429(
    client: TestClient, library: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Admission happens before headers: a queued-out stream is 429, never 200."""
    monkeypatch.setattr(client.app.state.speech, "stream", _full())
    _assert_429(client.post("/v1/audio/speech", json={**BODY, "stream": True}))


def test_api_speak_queue_full_429(
    client: TestClient, library: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client.app.state.speech, "synthesize", _full())
    _assert_429(client.post("/api/speak", json=BODY))
    assert client.app.state.takes.list() == []  # nothing was recorded


def test_api_speak_stream_queue_full_429(
    client: TestClient, library: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client.app.state.speech, "stream", _full())
    _assert_429(client.post("/api/speak", json={**BODY, "stream": True}))
