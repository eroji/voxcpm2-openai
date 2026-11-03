"""Voice library CRUD API (M2-T4, M2-T18): R-010, R-024, R-041, Q-010.

R-011 (Qwen3 import) is superseded by SPEC v0.2.0: voices are created only via
Clone/Design or the API clone route, so no import endpoint exists.
"""

from __future__ import annotations

import json
import zipfile

import numpy as np
import pytest
from app.config import Settings
from app.store import VoiceStore
from fastapi.testclient import TestClient

RATE = 24000
REF_TEXT = "Thanks for waiting. Everything checks out."


def tone(seconds: float = 1.0) -> np.ndarray:
    t = np.linspace(0, seconds, int(RATE * seconds), endpoint=False)
    return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


@pytest.fixture
def seeded(client: TestClient) -> VoiceStore:
    """Seed through the app's own store so index and HTTP see one instance."""
    store: VoiceStore = client.app.state.store
    for name, language in (("ashley-en", "en"), ("ashley-ja", "ja")):
        store.create(
            name,
            tone(),
            RATE,
            ref_text=REF_TEXT,
            language=language,
            consent="uploaded-via-studio",
        )
    return store


def test_list_returns_full_summaries(client: TestClient, seeded: VoiceStore):
    response = client.get("/api/voices")
    assert response.status_code == 200, response.text
    voices = response.json()["voices"]
    assert [v["key"] for v in voices] == ["ashley-en", "ashley-ja"]
    for voice in voices:
        assert voice["ref_text"] == REF_TEXT
        assert voice["ref_duration_s"] == pytest.approx(1.0, abs=0.05)
        assert set(voice) == {
            "key",
            "name",
            "language",
            "source",
            "ref_duration_s",
            "ref_text",
            "created_at",
        }


def test_audio_returns_riff_wav(client: TestClient, seeded: VoiceStore):
    response = client.get("/api/voices/ashley-en/audio")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.content[:4] == b"RIFF"


def test_patch_ref_text_and_language(client: TestClient, seeded: VoiceStore):
    response = client.patch(
        "/api/voices/ashley-en", json={"ref_text": "New transcript.", "language": "ja"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["key"] == "ashley-en"
    assert body["ref_text"] == "New transcript."
    assert body["language"] == "ja"
    assert seeded.get("ashley-en").ref_text == "New transcript."


def test_patch_empty_body_is_400(client: TestClient, seeded: VoiceStore):
    response = client.patch("/api/voices/ashley-en", json={})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "empty_patch"


def test_rename_updates_index_order(client: TestClient, seeded: VoiceStore, settings: Settings):
    response = client.patch("/api/voices/ashley-en", json={"name": "Zed"})
    assert response.status_code == 200, response.text
    assert response.json()["key"] == "zed"
    assert response.json()["name"] == "Zed"
    index = json.loads((settings.data_dir / "voices.json").read_text("utf-8"))
    assert index["order"] == ["ashley-ja", "zed"]
    assert not (settings.voices_dir / "ashley-en.safetensors").exists()
    assert (settings.voices_dir / "zed.safetensors").exists()


def test_rename_conflict_returns_409(client: TestClient, seeded: VoiceStore):
    response = client.patch("/api/voices/ashley-en", json={"name": "ashley-ja"})
    assert response.status_code == 409


def test_delete_writes_backup_zip(client: TestClient, seeded: VoiceStore, settings: Settings):
    response = client.delete("/api/voices/ashley-en")
    assert response.status_code == 200
    assert response.json() == {"deleted": "ashley-en"}
    zips = sorted(settings.backups_dir.glob("voices-*delete-ashley-en*.zip"))
    assert zips, "no backup zip written by DELETE"
    with zipfile.ZipFile(zips[-1]) as zf:
        assert "voices/ashley-en.safetensors" in zf.namelist()
    assert not (settings.voices_dir / "ashley-en.safetensors").exists()
    assert [v["key"] for v in client.get("/api/voices").json()["voices"]] == ["ashley-ja"]


def test_import_route_is_gone(client: TestClient, seeded: VoiceStore):
    """SPEC v0.2.0 §7: no import endpoint. `/api/voices/{name}` still matches the path, so
    Starlette answers 405 (not 404); either way nothing is imported."""
    response = client.post("/api/voices/import")
    assert response.status_code in (404, 405), response.status_code
    assert not any(
        getattr(route, "path", "") == "/api/voices/import" for route in client.app.routes
    )
    keys = [v["key"] for v in client.get("/api/voices").json()["voices"]]
    assert keys == ["ashley-en", "ashley-ja"]


@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("get", "/api/voices/ghost/audio", None),
        ("patch", "/api/voices/ghost", {"language": "en"}),
        ("delete", "/api/voices/ghost", None),
    ],
)
def test_unknown_voice_is_404_on_all_name_routes(
    client: TestClient, seeded: VoiceStore, method: str, url: str, body: dict | None
):
    response = client.request(method.upper(), url, json=body)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "voice_not_found"
