"""M2-T3/M2-T11/M2-T14/M2-T21: /api/speak + /api/takes — persistence, streaming, formats, wave."""

from __future__ import annotations

import base64
import io
import time
from typing import Any

import numpy as np
import pytest
import soundfile as sf
from app import audio
from app.main import create_app
from fastapi.testclient import TestClient

NO_VOICES = "No voices exist yet. Create one in the Studio (Clone or Design) first."


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


def _design_take(settings: Any) -> dict[str, Any]:
    """Design candidate written straight to disk (kind="design", Q-007 extras)."""
    from app.store import TakeStore

    return TakeStore(settings).create(
        np.full(4800, 0.1, dtype=np.float32),
        48000,
        voice="design",
        text="design sample",
        kind="design",
        extra={"design_prompt": "warm narrator", "design_seed": 7},
    )


def test_takes_list_filters_by_kind(client: Any, library: Any, settings: Any) -> None:
    studio = _speak(client).json()["id"]
    design = _design_take(settings)["id"]
    assert [t["id"] for t in client.get("/api/takes").json()["takes"]] == [studio]
    assert [t["id"] for t in client.get("/api/takes?kind=design").json()["takes"]] == [design]
    assert {t["id"] for t in client.get("/api/takes?kind=all").json()["takes"]} == {
        studio,
        design,
    }


def test_take_is_48k_when_sample_rate_configured_24k(
    client: Any, library: Any, settings: Any
) -> None:
    # SPEC §5: takes are 48 kHz whatever SAMPLE_RATE or the request asks for.
    settings.sample_rate = 24000
    take = _speak(client, sample_rate=24000).json()
    _, rate = audio.read_audio(client.get(take["audio_url"]).content)
    assert rate == 48000

    resp = _speak(client, stream=True, sample_rate=24000)
    assert resp.headers["X-Sample-Rate"] == "24000"  # the caller still gets 24 kHz
    saved = _wait_for_take(client, resp.headers["X-Take-Id"])
    _, rate = audio.read_audio(client.get(saved["audio_url"]).content)
    assert rate == 48000


def test_save_design_take_as_voice(client: Any, library: Any, settings: Any, store: Any) -> None:
    take = _design_take(settings)
    resp = client.post(
        f"/api/takes/{take['id']}/save-as-voice", json={"name": "Designed-Voice", "language": "en"}
    )
    assert resp.status_code == 200
    assert resp.json()["source"] == "designed"
    voice = store.get("designed-voice")
    assert voice.consent == "own-generated-voice"
    assert voice.metadata["design_prompt"] == "warm narrator"
    assert voice.metadata["design_seed"] == "7"


def test_save_as_voice_uses_take_text(client: Any, library: Any, store: Any) -> None:
    take = _speak(client).json()
    resp = client.post(
        f"/api/takes/{take['id']}/save-as-voice", json={"name": "New-Voice", "language": "ja"}
    )
    assert resp.status_code == 200
    voice = resp.json()
    assert voice["key"] == "new-voice"
    assert voice["source"] == "uploaded"
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


def _data_uri(seconds: float = 1.0, rate: int = 24000) -> str:
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    buf = io.BytesIO()
    sf.write(buf, (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), rate, format="WAV")
    return "data:audio/wav;base64," + base64.b64encode(buf.getvalue()).decode()


def test_preview_non_stream_returns_audio_and_no_take(client: Any, library: Any) -> None:
    # Q-023/Q-024: Studio preview = /api/speak?save=false, format honored, nothing stored.
    resp = client.post(
        "/api/speak?save=false",
        json={
            "model": "voxcpm2",
            "voice": "ashley-en",
            "input": "hello there",
            "response_format": "mp3",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("audio/mpeg")
    assert resp.headers["X-Voice"] == "ashley-en"
    assert resp.headers["X-Sample-Rate"] == "48000"
    assert "X-Take-Id" not in resp.headers
    assert resp.content[:3] == b"ID3" or resp.content[0:2] == b"\xff\xfb"  # mp3 frame/tag
    assert client.get("/api/takes").json()["takes"] == []


def test_preview_stream_returns_audio_and_no_take(client: Any, library: Any) -> None:
    resp = client.post(
        "/api/speak?save=false",
        json={
            "model": "voxcpm2",
            "voice": "ashley-en",
            "input": "hello there",
            "response_format": "pcm",
            "stream": True,
        },
    )
    assert resp.status_code == 200
    assert resp.headers["X-Voice"] == "ashley-en"
    assert resp.headers["X-Sample-Rate"] == "48000"
    assert "X-Take-Id" not in resp.headers
    assert len(resp.content) % 2 == 0 and len(resp.content) > 0
    assert client.get("/api/takes").json()["takes"] == []


def test_preview_with_ref_audio_is_ad_hoc_and_stores_nothing(
    client: Any, library: Any, store: Any
) -> None:
    # Q-023: "Test a line" needs only Studio auth — ref_audio, no voice, no take.
    before = len(store.list())
    resp = client.post(
        "/api/speak?save=false",
        json={
            "model": "voxcpm2",
            "voice": "test-line",
            "input": "checking the line",
            "ref_audio": _data_uri(),
            "ref_text": "Thanks for waiting.",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("audio/wav")
    assert "X-Take-Id" not in resp.headers
    assert len(store.list()) == before
    assert client.get("/api/takes").json()["takes"] == []


def test_saved_take_records_format_and_plays_back_transcoded(client: Any, library: Any) -> None:
    # Q-024: the take records the requested format; disk stays 48 kHz WAV; playback transcodes.
    take = _speak(client, response_format="mp3").json()
    assert take["format"] == "mp3"
    samples, rate = audio.read_audio(client.get(take["audio_url"]).content)
    assert rate == 48000

    mp3 = client.get(f"{take['audio_url']}?format=mp3")
    assert mp3.status_code == 200
    assert mp3.headers["content-type"].startswith("audio/mpeg")

    pcm = client.get(f"{take['audio_url']}?format=pcm")
    assert pcm.status_code == 200
    assert pcm.headers["content-type"].startswith("audio/pcm")
    assert len(pcm.content) % 2 == 0

    assert client.get(f"{take['audio_url']}?format=bogus").status_code == 400


def test_take_audio_format_falls_back_to_stored_format(client: Any, library: Any) -> None:
    # CONTRACTS §2: no ?format= → the take's stored format, wav when none was recorded.
    take = _speak(client, response_format="flac").json()
    assert take["format"] == "flac"
    assert client.get(take["audio_url"]).headers["content-type"].startswith("audio/flac")
    legacy = _speak(client).json()
    assert legacy["format"] == "wav"
    assert client.get(legacy["audio_url"]).headers["content-type"].startswith("audio/wav")


def test_take_speed_is_recorded_and_applied_on_playback(client: Any, library: Any) -> None:
    # Speak's Speed slider: disk stays unstretched, the take plays back at the chosen tempo.
    take = _speak(client, speed=0.9).json()
    assert take["speed"] == 0.9
    assert abs(take["duration_s"] - 0.6) < 0.05  # stored WAV is unstretched
    samples, rate = audio.read_audio(client.get(take["audio_url"]).content)
    assert abs(audio.duration_s(samples, rate) - 0.6 / 0.9) < 0.05
    plain = _speak(client).json()
    assert plain["speed"] == 1.0
    samples, rate = audio.read_audio(client.get(plain["audio_url"]).content)
    assert abs(audio.duration_s(samples, rate) - 0.6) < 0.05


# --- M1-T18: empty library + warm-only routing (SPEC §4/§8) -------------------------------


def test_speak_empty_library_is_no_voices_400(client: Any) -> None:
    resp = _speak(client)
    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["code"] == "no_voices"
    assert error["message"] == NO_VOICES


def test_preview_with_ref_audio_needs_no_stored_voice(client: Any) -> None:
    # SPEC §8: the empty-library guard is for stored voices only; Test-a-line still works.
    resp = client.post(
        "/api/speak?save=false",
        json={
            "model": "voxcpm2",
            "voice": "test-line",
            "input": "checking the line",
            "ref_audio": _data_uri(),
            "ref_text": "Thanks for waiting.",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("audio/wav")


def _unwarm_client(settings: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.warmup import WarmupService

    async def failing_run(self: WarmupService) -> bool:
        self.warm = False
        self.last_error = "warm-up failed"
        return False

    monkeypatch.setattr(WarmupService, "run", failing_run)
    return TestClient(create_app(settings))


def test_speak_before_ready_is_503_with_retry_after(settings: Any, monkeypatch: Any) -> None:
    with _unwarm_client(settings, monkeypatch) as c:
        assert c.get("/ready").status_code == 503
        resp = _speak(c)
        assert resp.status_code == 503
        assert resp.headers["Retry-After"] == "5"
        assert resp.json()["error"]["code"] == "engine_not_ready"
        assert c.app.state.engine.stats().rtf is None  # SPEC §4: not a sample


# --- M2-T21: session-scoped takes (X-Studio-Session) ---------------------------------------


def _speak_as(client: Any, session: str | None, **fields: Any) -> Any:
    headers = {"X-Studio-Session": session} if session is not None else {}
    body = {"model": "voxcpm2", "voice": "ashley-en", "input": "hello there", **fields}
    return client.post("/api/speak", json=body, headers=headers)


def _take_ids(client: Any, session: str | None = None, kind: str | None = None) -> list[str]:
    headers = {"X-Studio-Session": session} if session is not None else {}
    url = "/api/takes" + (f"?kind={kind}" if kind else "")
    return [t["id"] for t in client.get(url, headers=headers).json()["takes"]]


def test_same_session_sees_only_its_takes(client: Any, library: Any) -> None:
    mine = _speak_as(client, "sess-a").json()["id"]
    _speak_as(client, None)  # another (headerless) caller
    assert _take_ids(client, "sess-a") == [mine]


def test_two_sessions_are_isolated(client: Any, library: Any) -> None:
    a = _speak_as(client, "sess-a").json()["id"]
    b = _speak_as(client, "sess-b").json()["id"]
    assert _take_ids(client, "sess-a") == [a]
    assert _take_ids(client, "sess-b") == [b]


def test_no_session_header_returns_all_takes(client: Any, library: Any) -> None:
    a = _speak_as(client, "sess-a").json()["id"]
    b = _speak_as(client, "sess-b").json()["id"]
    assert set(_take_ids(client)) == {a, b}


def test_invalid_session_header_is_treated_as_absent(client: Any, library: Any) -> None:
    a = _speak_as(client, "sess-a").json()["id"]
    for bad in ("no spaces", "x" * 65, "sess/../etc", ""):
        assert _take_ids(client, bad) == [a]
    long = _speak_as(client, "x" * 64).json()["id"]  # 64 chars is the boundary, valid
    assert _take_ids(client, "x" * 64) == [long]


def test_query_param_session_works_like_the_header(client: Any, library: Any) -> None:
    a = _speak_as(client, "sess-a").json()["id"]
    _speak_as(client, "sess-b")
    assert [t["id"] for t in client.get("/api/takes?session=sess-a").json()["takes"]] == [a]
    assert client.get("/api/takes?session=no%20good").json()["takes"]  # invalid → all
    assert len(client.get("/api/takes?session=no%20good").json()["takes"]) == 2


def test_streamed_take_carries_the_session(client: Any, library: Any) -> None:
    resp = _speak_as(client, "sess-a", stream=True, response_format="pcm")
    assert resp.status_code == 200
    take = _wait_for_take(client, resp.headers["X-Take-Id"])  # headerless list sees all
    assert _take_ids(client, "sess-a") == [take["id"]]
    assert _take_ids(client, "sess-b") == []


def test_design_takes_are_not_session_bound(client: Any, library: Any, settings: Any) -> None:
    studio = _speak_as(client, "sess-a").json()["id"]
    design = _design_take(settings)["id"]
    assert _take_ids(client, "sess-b") == []
    assert _take_ids(client, "sess-b", kind="design") == [design]
    assert _take_ids(client, "sess-b", kind="all") == [design]
    assert set(_take_ids(client, "sess-a", kind="all")) == {studio, design}


def test_preview_with_session_stores_nothing(client: Any, library: Any) -> None:
    resp = client.post(
        "/api/speak?save=false",
        json={"model": "voxcpm2", "voice": "ashley-en", "input": "hi"},
        headers={"X-Studio-Session": "sess-a"},
    )
    assert resp.status_code == 200
    assert _take_ids(client, "sess-a") == []


def test_take_on_disk_records_the_session(client: Any, library: Any, settings: Any) -> None:
    import json

    take = _speak_as(client, "sess-a").json()
    meta = json.loads((settings.takes_dir / f"{take['id']}.json").read_text("utf-8"))
    assert meta["session"] == "sess-a"


def test_preview_before_ready_is_503(settings: Any, monkeypatch: Any) -> None:
    # save=false is warm-only too: the Studio shows "warming up", not a stale take.
    with _unwarm_client(settings, monkeypatch) as c:
        resp = c.post(
            "/api/speak?save=false",
            json={
                "model": "voxcpm2",
                "voice": "test-line",
                "input": "checking the line",
                "ref_audio": _data_uri(),
            },
        )
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "engine_not_ready"
