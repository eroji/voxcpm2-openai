"""``/v1`` OpenAI-compatible API tests (M1-T2, SPEC §6)."""

from __future__ import annotations

import time

import numpy as np
import pytest
from app.audio import to_wav_data_uri
from app.config import Settings
from app.engine import EngineClient
from app.main import create_app
from fastapi.testclient import TestClient

from tests.test_engine import _serve, _status_app

BODY = {"model": "tts-1", "voice": "ashley-en", "input": "hello"}
NO_VOICES = "No voices exist yet. Create one in the Studio (Clone or Design) first."

_TONE = (0.2 * np.sin(2 * np.pi * 220 * np.linspace(0, 0.2, 9600, endpoint=False))).astype(
    np.float32
)
REF_URI = to_wav_data_uri(_TONE, 48000)


def assert_openai_error(response, status: int) -> dict:
    assert response.status_code == status
    error = response.json()["error"]
    assert set(error) == {"message", "type", "param", "code"}
    assert isinstance(error["message"], str) and error["message"]
    return error


def _wait_warm(client, timeout: float = 5.0) -> bool:
    """Apps built outside the `client` fixture must wait for warm-up too (M1-T18)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.get("/ready").status_code == 200:
            return True
        time.sleep(0.05)
    return False


def _force_warm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Open the warm gate without a working synthesis, for tests about something else.

    Setting ``warmup.warm`` from the test would race with the real warm-up attempt,
    which clears it again when it fails.
    """
    from app.warmup import WarmupService

    async def run(self: WarmupService) -> bool:
        self.warm = self.ever_warm = True
        self.last_error = None
        return True

    monkeypatch.setattr(WarmupService, "run", run)


def test_speech_wav_ok(client, library):
    r = client.post("/v1/audio/speech", json=BODY)
    assert r.status_code == 200
    assert r.content[:4] == b"RIFF"
    assert r.headers["content-type"].startswith("audio/wav")
    assert int.from_bytes(r.content[24:28], "little") == 48000
    assert r.headers["X-Voice"].lower() == "ashley-en"
    assert r.headers["X-Sample-Rate"] == "48000"
    assert float(r.headers["X-Audio-Duration"]) == pytest.approx(0.6, abs=0.05)
    assert 0 < float(r.headers["X-RTF"]) < 1


def test_voice_is_case_insensitive(client, library):
    r = client.post("/v1/audio/speech", json={**BODY, "voice": "ASHLEY-EN"})
    assert r.status_code == 200
    assert r.headers["X-Voice"].lower() == "ashley-en"


def test_unknown_voice_400_lists_names(client, library):
    r = client.post("/v1/audio/speech", json={**BODY, "voice": "nope"})
    error = assert_openai_error(r, 400)
    assert "ashley-en" in error["message"]
    assert error["param"] == "voice"


def test_input_too_long_400(client, library):
    r = client.post("/v1/audio/speech", json={**BODY, "input": "x" * 4001})
    assert_openai_error(r, 400)


def test_bad_response_format_400(client, library):
    r = client.post("/v1/audio/speech", json={**BODY, "response_format": "exe"})
    assert_openai_error(r, 400)


def test_unknown_fields_accepted(client, library):
    r = client.post("/v1/audio/speech", json={**BODY, "task_type": "sound_effect", "seed": 7})
    assert r.status_code == 200


@pytest.mark.parametrize(
    "fmt,ctype",
    [
        ("wav", "audio/wav"),
        ("pcm", "audio/pcm"),
        ("mp3", "audio/mpeg"),
        ("opus", "audio/ogg"),
        ("flac", "audio/flac"),
        ("aac", "audio/aac"),
    ],
)
def test_response_formats(client, library, fmt, ctype):
    r = client.post("/v1/audio/speech", json={**BODY, "response_format": fmt})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(ctype)
    assert len(r.content) > 0


def test_pcm_is_two_bytes_per_sample(client, library):
    r = client.post("/v1/audio/speech", json={**BODY, "response_format": "pcm"})
    samples = round(float(r.headers["X-Audio-Duration"]) * int(r.headers["X-Sample-Rate"]))
    assert len(r.content) == samples * 2


def test_sample_rate_override(client, library):
    r = client.post("/v1/audio/speech", json={**BODY, "sample_rate": 24000})
    assert r.status_code == 200
    assert r.headers["X-Sample-Rate"] == "24000"
    assert int.from_bytes(r.content[24:28], "little") == 24000


def test_speed_halves_duration(client, library):
    slow = client.post("/v1/audio/speech", json=BODY)
    fast = client.post("/v1/audio/speech", json={**BODY, "speed": 2.0})
    assert float(fast.headers["X-Audio-Duration"]) == pytest.approx(
        float(slow.headers["X-Audio-Duration"]) / 2, abs=0.05
    )


def test_missing_model_400(client, library):
    body = {k: v for k, v in BODY.items() if k != "model"}
    error = assert_openai_error(client.post("/v1/audio/speech", json=body), 400)
    assert "model" in error["message"] or error["param"] == "model"


def test_models_card(client):
    """SPEC §6: exactly one card; aliases are accepted on request, not listed."""
    r = client.get("/v1/models")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    data = body["data"]
    assert [m["id"] for m in data] == ["voxcpm2"]
    assert all(m["object"] == "model" for m in data)


def test_non_ascii_display_name_header(client, store):
    t = np.linspace(0, 1.0, 24000, endpoint=False)
    store.create(
        "アシュリー",
        (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32),
        24000,
        ref_text="konnichiwa",
        language="ja",
        consent="uploaded-via-studio",
        key="ashley-ja2",
    )
    r = client.post("/v1/audio/speech", json={**BODY, "voice": "ashley-ja2"})
    assert r.status_code == 200
    assert r.headers["X-Voice"] == "ashley-ja2"


def test_unknown_api_path_404_error_shape(client):
    assert_openai_error(client.get("/api/nope"), 404)


def test_wrong_method_405_error_shape(client):
    assert_openai_error(client.get("/v1/audio/speech"), 405)


def test_voices_list(client, library):
    r = client.get("/v1/audio/voices")
    assert r.status_code == 200
    voices = r.json()["voices"]
    assert {v["name"] for v in voices} == {"ashley-en", "ashley-ja"}
    assert all(set(v) == {"name", "language", "source"} for v in voices)


def test_streaming_still_works(client, library):
    with client.stream(
        "POST", "/v1/audio/speech", json={**BODY, "stream": True, "response_format": "pcm"}
    ) as r:
        assert r.status_code == 200
        assert r.headers["X-Voice"].lower() == "ashley-en"
        assert sum(len(c) for c in r.iter_raw()) > 0


def test_api_keys_gate(settings, library):
    secured = Settings(
        data_dir=settings.data_dir,
        engine_url=settings.engine_url,
        static_dir=settings.static_dir,
        asset_dir=settings.asset_dir,
        api_keys="k1",
    )
    with TestClient(create_app(secured)) as c:
        assert _wait_warm(c), "warm-up never finished: /v1 is warm-only"
        assert_openai_error(c.post("/v1/audio/speech", json=BODY), 401)
        auth = {"Authorization": "Bearer k1"}
        assert c.post("/v1/audio/speech", json=BODY, headers=auth).status_code == 200
        assert c.get("/v1/models").status_code == 401
        assert c.get("/v1/models", headers=auth).status_code == 200


def test_upstream_429_becomes_429_with_retry_after(settings, library, monkeypatch):
    url, server, thread = _serve(_status_app(429, retry_after="7"))
    try:
        _force_warm(monkeypatch)  # this engine can never warm up: test the mapping, not the gate
        with TestClient(create_app(settings, engine=EngineClient(url))) as c:
            r = c.post("/v1/audio/speech", json=BODY)
            error = assert_openai_error(r, 429)
            assert error["type"] == "rate_limit_error"
            assert r.headers["Retry-After"] == "7"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_upstream_503_becomes_503_engine_not_ready(settings, library, monkeypatch):
    url, server, thread = _serve(_status_app(503))
    try:
        _force_warm(monkeypatch)  # see the 429 test
        with TestClient(create_app(settings, engine=EngineClient(url))) as c:
            r = c.post("/v1/audio/speech", json=BODY)
            error = assert_openai_error(r, 503)
            assert error["code"] == "engine_not_ready"
            assert r.headers["Retry-After"] == "5"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_voice_header_injection_neutralized(client, library):
    r = client.post(
        "/v1/audio/speech", json={**BODY, "voice": "a\r\nX-Evil: 1", "ref_audio": REF_URI}
    )
    assert r.status_code == 200
    assert r.headers["X-Voice"] == "ad-hoc"
    assert "X-Evil" not in r.headers


def test_long_voice_label_neutralized(client, library):
    r = client.post("/v1/audio/speech", json={**BODY, "voice": "v" * 1000, "ref_audio": REF_URI})
    assert r.status_code == 200
    assert r.headers["X-Voice"] == "ad-hoc"


# --- M1-T18: empty library + warm-only routing (SPEC §4/§8) -------------------------------


def test_empty_library_is_no_voices_400(client):
    """Nothing stored is a setup problem, not an unknown voice (SPEC §8 exact copy)."""
    r = client.post("/v1/audio/speech", json=BODY)
    error = assert_openai_error(r, 400)
    assert error["code"] == "no_voices"
    assert error["message"] == NO_VOICES


def test_empty_library_ad_hoc_ref_audio_still_works(client):
    """A preview with inline reference audio needs no stored voice."""
    r = client.post("/v1/audio/speech", json={**BODY, "voice": "label", "ref_audio": REF_URI})
    assert r.status_code == 200
    assert r.headers["X-Voice"] == "label"
    assert r.content[:4] == b"RIFF"


def test_before_ready_v1_is_503_and_never_reaches_the_engine(settings, library, monkeypatch):
    from app.warmup import WarmupService

    async def failing_run(self: WarmupService) -> bool:
        self.warm = False
        self.last_error = "warm-up failed"
        return False

    monkeypatch.setattr(WarmupService, "run", failing_run)
    with TestClient(create_app(settings)) as c:
        assert c.get("/ready").status_code == 503
        r = c.post("/v1/audio/speech", json=BODY)
        error = assert_openai_error(r, 503)
        assert error["code"] == "engine_not_ready"
        assert r.headers["Retry-After"] == "5"
        # SPEC §4: a request that started before /ready went green is not a sample.
        assert c.app.state.engine.stats().rtf is None
