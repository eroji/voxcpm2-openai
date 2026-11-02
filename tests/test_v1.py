"""``/v1`` OpenAI-compatible API tests (M1-T2, SPEC §6)."""

from __future__ import annotations

import pytest
from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient

BODY = {"model": "tts-1", "voice": "ashley-en", "input": "hello"}


def assert_openai_error(response, status: int) -> dict:
    assert response.status_code == status
    error = response.json()["error"]
    assert set(error) == {"message", "type", "param", "code"}
    assert isinstance(error["message"], str) and error["message"]
    return error


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


def test_models_card(client):
    r = client.get("/v1/models")
    assert r.status_code == 200
    assert r.json() == {
        "object": "list",
        "data": [{"id": "voxcpm2", "object": "model", "created": 0, "owned_by": "openbmb"}],
    }


def test_voices_list(client, library):
    r = client.get("/v1/audio/voices")
    assert r.status_code == 200
    voices = r.json()["voices"]
    assert {v["name"] for v in voices} == {"ashley-en", "ashley-ja"}
    assert all(set(v) == {"name", "language", "source"} for v in voices)


def test_api_keys_gate(settings, library):
    secured = Settings(
        data_dir=settings.data_dir,
        import_dir=settings.import_dir,
        engine_url=settings.engine_url,
        static_dir=settings.static_dir,
        asset_dir=settings.asset_dir,
        api_keys="k1",
    )
    with TestClient(create_app(secured)) as c:
        assert_openai_error(c.post("/v1/audio/speech", json=BODY), 401)
        auth = {"Authorization": "Bearer k1"}
        assert c.post("/v1/audio/speech", json=BODY, headers=auth).status_code == 200
        assert c.get("/v1/models").status_code == 401
        assert c.get("/v1/models", headers=auth).status_code == 200
