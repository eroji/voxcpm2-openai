"""Clone + transcribe API (M2-T2): R-024..R-026, R-041..R-044."""

from __future__ import annotations

import io
import socket
import threading
import time

import numpy as np
import pytest
import uvicorn
from app import audio
from app.config import Settings
from app.store import VoiceStore
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

RATE = 24000


def tone(seconds: float, rate: int = RATE, amp: float = 0.3) -> np.ndarray:
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def wav(seconds: float = 0.0, samples: np.ndarray | None = None) -> bytes:
    return audio.wav_bytes(samples if samples is not None else tone(seconds), RATE)


def clone_files(
    data: bytes,
    *,
    name: str = "clone-test",
    language: str = "en",
    ref_text: str = "hello there",
    consent: str | None = "true",
    filename: str = "ref.wav",
) -> dict:
    files = {
        "file": (filename, io.BytesIO(data), "audio/wav"),
        "name": (None, name),
        "language": (None, language),
        "ref_text": (None, ref_text),
    }
    if consent is not None:
        files["consent"] = (None, consent)
    return files


def test_clone_stores_voice_with_normalized_consent(client: TestClient, store: VoiceStore):
    response = client.post("/api/voices/clone", files=clone_files(wav(6.0), consent="TRUE"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "clone-test"
    assert body["language"] == "en"
    assert body["source"] == "uploaded"
    assert 3.0 <= body["ref_duration_s"] <= 30.0
    assert body["ref_text"] == "hello there"
    voice = store.get("clone-test")
    assert voice.consent == "uploaded-via-studio"
    assert voice.metadata["original_filename"] == "ref.wav"


def test_clone_duplicate_returns_409(client: TestClient):
    assert client.post("/api/voices/clone", files=clone_files(wav(6.0))).status_code == 200
    response = client.post("/api/voices/clone", files=clone_files(wav(6.0)))
    assert response.status_code == 409


@pytest.mark.parametrize("seconds", [2.0, 31.0])
def test_clone_rejects_out_of_range_duration(client: TestClient, seconds: float):
    response = client.post("/api/voices/clone", files=clone_files(wav(seconds)))
    assert response.status_code == 400


def test_clone_requires_consent(client: TestClient):
    response = client.post("/api/voices/clone", files=clone_files(wav(6.0), consent=None))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "consent_required"


def test_clone_rejects_non_audio_named_wav(client: TestClient):
    response = client.post("/api/voices/clone", files=clone_files(b"not audio at all"))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_audio"


def test_clone_rejects_oversized_upload(client: TestClient, settings: Settings, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes", 1000)
    response = client.post("/api/voices/clone", files=clone_files(wav(6.0)))
    assert response.status_code == 400
    assert settings.max_upload_bytes == 1000  # monkeypatch restores this after the test


def test_clone_trims_silence_and_peak_normalizes(client: TestClient, store: VoiceStore):
    samples = np.concatenate(
        [np.zeros(RATE, dtype=np.float32), tone(5.0), np.zeros(RATE, dtype=np.float32)]
    )
    response = client.post("/api/voices/clone", files=clone_files(wav(samples=samples)))
    assert response.status_code == 200, response.text
    stored, rate = store.read_audio("clone-test")
    assert len(stored) / rate < 7.0
    assert float(np.max(np.abs(stored))) == pytest.approx(10 ** (-1.0 / 20.0), abs=0.01)


def test_transcribe_unconfigured_returns_503(client: TestClient, settings: Settings):
    assert settings.stt_url == ""
    response = client.post(
        "/api/transcribe", files={"file": ("ref.wav", io.BytesIO(wav(6.0)), "audio/wav")}
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "stt_not_configured"


@pytest.fixture
def fake_stt():
    app = FastAPI()

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(request: Request) -> dict[str, str]:
        form = await request.form()
        assert "file" in form
        return {"text": "hello"}

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.02)
    else:
        raise RuntimeError("fake stt did not start")
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def test_transcribe_forwards_to_stt(client: TestClient, settings: Settings, fake_stt: str):
    settings.stt_url = f"{fake_stt}/v1"
    response = client.post(
        "/api/transcribe", files={"file": ("ref.wav", io.BytesIO(wav(6.0)), "audio/wav")}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"text": "hello"}
