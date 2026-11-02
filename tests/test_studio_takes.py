"""M2-T3: /api/speak + /api/takes — take persistence, streaming, wave, save-as-voice."""

from __future__ import annotations

import time
from typing import Any

from app import audio


def _speak(client: Any, **fields: Any) -> Any:
    body = {"voice": "ashley-en", "input": "hello there", **fields}
    return client.post("/api/speak", json=body)


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
    assert abs(pcm_duration - 0.6) < 0.05

    deadline = time.time() + 3.0
    while time.time() < deadline:
        items = client.get("/api/takes").json()["takes"]
        if any(item["id"] == take_id for item in items):
            break
        time.sleep(0.05)
    take = next(item for item in items if item["id"] == take_id)
    assert abs(take["duration_s"] - pcm_duration) < 0.01
    assert take["language"] == "en"
    assert take["text"] == "hello there"


def test_speak_rejects_too_long_input(client: Any, library: Any) -> None:
    resp = client.post("/api/speak", json={"voice": "ashley-en", "input": "x" * 4001})
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
