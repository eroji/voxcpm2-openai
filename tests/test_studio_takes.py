"""M2-T3/M2-T11: /api/speak + /api/takes — persistence, streaming, wave, save-as-voice."""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pytest
from app import audio


def _speak(client: Any, **fields: Any) -> Any:
    body = {"model": "voxcpm2", "voice": "ashley-en", "input": "hello there", **fields}
    return client.post("/api/speak", json=body)


def _wait_for_take(client: Any, take_id: str, timeout: float = 5.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        items = client.get("/api/takes").json()["takes"]
        hit = next((item for item in items if item["id"] == take_id), None)
        if hit is not None:
            return hit
        time.sleep(0.05)
    raise AssertionError(f"take {take_id} was never saved")


def _keyed_voice(store: Any) -> Any:
    """Voice whose display name differs from its lookup key (SPEC §5 name vs key)."""
    t = np.linspace(0, 1.0, 24000, endpoint=False)
    return store.create(
        "Qwen A",
        (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32),
        24000,
        ref_text="Thanks for waiting. Everything checks out.",
        language="ja",
        consent="uploaded-via-studio",
        key="qwen-a",
    )


def test_speak_non_stream_returns_take_info(client: Any, library: Any) -> None:
    resp = _speak(client)
    assert resp.status_code == 200
    take = resp.json()
    assert take["voice"] == "ashley-en"
    assert take["language"] == "en"
    assert take["text"] == "hello there"
    assert take["kind"] == "studio"
    assert take["audio_url"] == f"/api/takes/{take['id']}/audio"
    assert take["wave_url"] == f"/api/takes/{take['id']}/wave"

    audio_resp = client.get(take["audio_url"])
    assert audio_resp.status_code == 200
    assert audio_resp.headers["content-type"].startswith("audio/wav")
    samples, rate = audio.read_audio(audio_resp.content)
    assert abs(audio.duration_s(samples, rate) - take["duration_s"]) < 0.01


def test_speak_non_stream_returns_take_metadata(client: Any, library: Any) -> None:
    # SPEC §7: /api/speak always returns take metadata + an audio URL, whatever format was asked.
    resp = _speak(client, response_format="mp3")
    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
    body = resp.json()
    assert body["id"] and body["audio_url"] and body["wave_url"]
    takes = client.get("/api/takes").json()["takes"]
    assert len(takes) == 1
    # The take on disk stays a 48 kHz WAV regardless of the requested container.
    samples, rate = audio.read_audio(client.get(takes[0]["audio_url"]).content)
    assert rate == 48000
    assert audio.duration_s(samples, rate) > 0.3


def test_speak_stream_advertises_take_and_saves_it(client: Any, library: Any) -> None:
    resp = _speak(client, stream=True, response_format="pcm")
    assert resp.status_code == 200
    take_id = resp.headers["X-Take-Id"]
    assert resp.headers["X-Voice"] == "ashley-en"
    assert resp.headers["X-Sample-Rate"] == "48000"
    assert len(resp.content) % 2 == 0
    pcm_duration = len(resp.content) / 2 / 48000

    take = _wait_for_take(client, take_id)
    assert abs(take["duration_s"] - pcm_duration) < 0.01
    assert take["language"] == "en"
    assert take["text"] == "hello there"


def test_speak_rejects_too_long_input(client: Any, library: Any) -> None:
    resp = client.post(
        "/api/speak", json={"model": "voxcpm2", "voice": "ashley-en", "input": "x" * 4001}
    )
    assert resp.status_code == 400
    assert "input" in resp.json()["error"]["message"]


def test_takes_list_is_newest_first(client: Any, library: Any) -> None:
    first = _speak(client, input="one").json()["id"]
    second = _speak(client, input="two").json()["id"]
    ids = [item["id"] for item in client.get("/api/takes").json()["takes"]]
    assert ids[:2] == [second, first]


def test_list_skips_take_with_missing_wav(client: Any, library: Any, settings: Any) -> None:
    take = _speak(client).json()
    (settings.takes_dir / f"{take['id']}.wav").unlink()
    assert client.get("/api/takes").json()["takes"] == []
    assert client.get(take["audio_url"]).status_code == 404


def test_take_wave_has_72_amps(client: Any, library: Any) -> None:
    take = _speak(client).json()
    amps = client.get(take["wave_url"]).json()["amps"]
    assert len(amps) == 72
    assert all(0.0 <= amp <= 1.0 for amp in amps)


def test_save_as_voice_uses_take_text(client: Any, library: Any, store: Any) -> None:
    take = _speak(client).json()
    resp = client.post(
        f"/api/takes/{take['id']}/save-as-voice", json={"name": "New-Voice", "language": "ja"}
    )
    assert resp.status_code == 200
    voice = resp.json()
    assert voice["key"] == "new-voice"
    assert voice["ref_text"] == take["text"]
    assert voice["language"] == "ja"
    assert store.get("new-voice").consent == "own-generated-voice"


def test_delete_take(client: Any, library: Any) -> None:
    take = _speak(client).json()
    assert client.delete(f"/api/takes/{take['id']}").json() == {"deleted": take["id"]}
    assert client.get(take["audio_url"]).status_code == 404
    ids = [item["id"] for item in client.get("/api/takes").json()["takes"]]
    assert take["id"] not in ids


def test_unknown_take_404_on_every_route(client: Any, library: Any) -> None:
    for resp in (
        client.get("/api/takes/nosuchtake/audio"),
        client.get("/api/takes/nosuchtake/wave"),
        client.delete("/api/takes/nosuchtake"),
        client.post("/api/takes/nosuchtake/save-as-voice", json={"name": "x1", "language": "en"}),
    ):
        assert resp.status_code == 404


def test_take_language_resolved_by_key_non_stream(client: Any, library: Any, store: Any) -> None:
    _keyed_voice(store)
    resp = _speak(client, voice="qwen-a")
    assert resp.status_code == 200
    assert resp.json()["language"] == "ja"


def test_take_language_resolved_by_key_stream(client: Any, library: Any, store: Any) -> None:
    _keyed_voice(store)
    resp = _speak(client, voice="qwen-a", stream=True)
    assert resp.status_code == 200
    assert resp.headers["X-Voice"] == "qwen-a"
    take = _wait_for_take(client, resp.headers["X-Take-Id"])
    assert take["language"] == "ja"


def test_take_is_native_rate_despite_rate_and_speed(client: Any, library: Any) -> None:
    # SPEC §5: takes are 48 kHz WAVs; caller sample_rate/speed never reach disk.
    take = _speak(client, sample_rate=24000, speed=2.0).json()
    samples, rate = audio.read_audio(client.get(take["audio_url"]).content)
    assert rate == 48000
    assert abs(take["duration_s"] - 0.6) < 0.05


@pytest.mark.parametrize("fmt", ["pcm", "wav", "mp3", "opus", "flac"])
def test_every_streamed_format_saves_a_take(client: Any, library: Any, fmt: str) -> None:
    resp = _speak(client, stream=True, response_format=fmt)
    assert resp.status_code == 200
    take = _wait_for_take(client, resp.headers["X-Take-Id"])
    samples, rate = audio.read_audio(client.get(take["audio_url"]).content)
    assert rate == 48000
    assert abs(audio.duration_s(samples, rate) - 0.6) < 0.1


def test_configured_max_input_chars_guard(client: Any, library: Any, settings: Any) -> None:
    # The SpeechRequest 4000-char cap must not mask the configured limit.
    settings.max_input_chars = 10
    resp = _speak(client, input="x" * 11)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "input_too_long"
