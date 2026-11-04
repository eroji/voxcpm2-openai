"""M4-T1: voice design backend — candidates + save (SPEC §7, Q-002/Q-007)."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest
from app import audio as audio_utils
from app.config import Settings
from app.engine import EngineClient
from app.models import SpeechRequest
from app.speech import SpeechService
from app.store import TakeStore
from fastapi.testclient import TestClient

DESCRIPTION = "A warm female voice, a calm narrator in her thirties."
SAMPLE = "Good morning, here is a sample of the voice."


def _candidates(client: TestClient, n: int = 3):
    return client.post(
        "/api/design/candidates",
        json={"description": DESCRIPTION, "sample_text": SAMPLE, "language": "en", "n": n},
    )


def _save(client: TestClient, candidate_id: str, name: str, language: str = "en"):
    return client.post(
        "/api/design/save",
        json={"candidate_id": candidate_id, "name": name, "language": language},
    )


def test_three_candidates(client: TestClient, settings: Settings, fake_engine) -> None:
    resp = _candidates(client)
    assert resp.status_code == 200
    items = resp.json()["candidates"]
    assert [c["index"] for c in items] == [1, 2, 3]
    seeds = [c["seed"] for c in items]
    assert len(set(seeds)) == 3
    assert all(1 <= s < 2**31 for s in seeds)
    for c in items:
        assert c["audio_url"] == f"/api/takes/{c['id']}/audio"
        assert c["wave_url"] == f"/api/takes/{c['id']}/wave"
        assert c["duration_s"] == pytest.approx(0.6, abs=0.05)
    design_takes = [t for t in TakeStore(settings).list() if t["kind"] == "design"]
    assert len(design_takes) == 3
    assert {t["design_seed"] for t in design_takes} == set(seeds)
    for take in design_takes:
        samples, _ = audio_utils.read_audio_file(TakeStore(settings).audio_path(take["id"]))
        assert float(np.max(np.abs(samples))) == pytest.approx(10 ** (-1 / 20), abs=0.01)
        assert take["voice"] == "design"
        assert take["text"] == SAMPLE
        assert take["design_prompt"] == DESCRIPTION
    last = fake_engine.state["last"]
    assert "ref_audio" not in last and "ref_text" not in last
    assert last["input"].startswith(f"({DESCRIPTION})")
    assert "instructions" not in last


def test_candidate_count_bounds(client: TestClient) -> None:
    assert _candidates(client, n=1).status_code == 200
    assert _candidates(client, n=5).status_code == 200
    assert _candidates(client, n=0).status_code == 400
    assert _candidates(client, n=6).status_code == 400


def test_save_designs_voice(
    client: TestClient, settings: Settings, store, library, fake_engine
) -> None:
    item = _candidates(client, n=1).json()["candidates"][0]
    resp = _save(client, item["id"], "designed-one")
    assert resp.status_code == 200
    summary = resp.json()
    assert summary["name"] == "designed-one"
    assert summary["source"] == "designed"
    assert summary["ref_text"] == SAMPLE

    voice = store.get("designed-one")
    assert voice.consent == "own-generated-voice"
    assert voice.metadata["design_prompt"] == DESCRIPTION
    assert voice.metadata["design_seed"] == str(item["seed"])

    # The designed voice must synthesize by name like any other voice. The /v1
    # router is still a stub in this wave (M1-T2), so exercise the same service
    # path SpeechService.synthesize that /v1/audio/speech will use.
    async def call() -> bytes:
        engine = EngineClient.from_settings(settings)
        await engine.start()
        try:
            result = await SpeechService(store, engine, settings).synthesize(
                SpeechRequest(model="voxcpm2", input="Hello there.", voice="designed-one")
            )
        finally:
            await engine.aclose()
        return result.audio

    assert asyncio.run(call())


def test_save_several_from_one_set(client: TestClient, store) -> None:
    first, second = _candidates(client, n=2).json()["candidates"]
    assert _save(client, first["id"], "designed-a").status_code == 200
    assert _save(client, second["id"], "designed-b").status_code == 200
    assert store.get("designed-a").metadata["design_seed"] == str(first["seed"])
    assert store.get("designed-b").metadata["design_seed"] == str(second["seed"])


def test_save_unknown_candidate(client: TestClient) -> None:
    assert _save(client, "doesnotexist", "whoever").status_code == 404


def test_save_rejects_plain_take(client: TestClient, settings: Settings) -> None:
    take = TakeStore(settings).create(
        np.zeros(48000, dtype=np.float32), 48000, voice="ashley-en", text="hi", kind="studio"
    )
    assert _save(client, take["id"], "whoever").status_code == 404


def test_save_duplicate_name_conflicts(client: TestClient, store, make_voice) -> None:
    make_voice("designed-one", "en")
    item = _candidates(client, n=1).json()["candidates"][0]
    assert _save(client, item["id"], "designed-one").status_code == 409
