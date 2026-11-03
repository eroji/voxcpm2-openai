"""M2-T20 — languages store + management API (SPEC §5a, §7).

Covers the seed file, the supported/enabled split, add/default/remove rules (including the
voice relabel/delete decisions and their backups), preset validation, supported-set drift,
and the voice-language enforcement on the Studio write paths.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from app import audio
from app.config import Settings
from app.languages import LanguageStore
from app.main import create_app
from app.store import VoiceStore
from fastapi.testclient import TestClient

RATE = 24000
ROW_KEYS = {"code", "name", "native", "default", "voices", "presets", "supported"}


def tone(seconds: float = 1.0) -> np.ndarray:
    t = np.linspace(0, seconds, int(RATE * seconds), endpoint=False)
    return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def make_voice(store: VoiceStore, name: str, language: str) -> Any:
    return store.create(
        name,
        tone(),
        RATE,
        ref_text="Thanks for waiting. Everything checks out.",
        language=language,
        consent="uploaded-via-studio",
    )


def codes(client: TestClient) -> list[str]:
    return [item["code"] for item in client.get("/api/languages").json()["languages"]]


# --------------------------------------------------------------------- seed + shapes


def test_seed_file_written_on_first_start(client: TestClient, settings: Settings) -> None:
    data = json.loads((settings.data_dir / "languages.json").read_text("utf-8"))
    assert data["default"] == "en"
    assert data["enabled"] == ["en", "ja", "ko", "zh"]
    assert len(data["presets"]["en"]) == 6
    assert data["presets"]["en"][0] == {
        "id": "greeting",
        "label": "Greeting",
        "text": data["presets"]["en"][0]["text"],
    }


def test_existing_seed_file_is_preserved(client: TestClient, settings: Settings) -> None:
    path = settings.data_dir / "languages.json"
    path.write_text(
        json.dumps({"default": "ja", "enabled": ["ja"], "presets": {"ja": []}}), "utf-8"
    )
    store = LanguageStore(settings, VoiceStore(settings))
    assert [entry["code"] for entry in store.entries()] == ["ja"]
    assert store.entries()[0]["default"] is True


def test_get_languages_shape(client: TestClient, store: VoiceStore) -> None:
    make_voice(store, "ashley-en", "en")
    make_voice(store, "mia-zh", "zh")
    languages = client.get("/api/languages").json()["languages"]
    assert [item["code"] for item in languages] == ["en", "ja", "ko", "zh"]  # default first
    for item in languages:
        assert set(item) == ROW_KEYS
        assert item["supported"] is True
    assert languages[0] == {
        "code": "en",
        "name": "English",
        "native": "English",
        "default": True,
        "voices": 1,
        "presets": 6,
        "supported": True,
    }
    assert languages[3]["code"] == "zh" and languages[3]["voices"] == 1


def test_supported_lists_30_with_native_names(client: TestClient) -> None:
    supported = client.get("/api/languages/supported").json()["languages"]
    assert len(supported) == 30
    assert all(set(item) == {"code", "name", "native"} for item in supported)
    by_code = {item["code"]: item for item in supported}
    assert by_code["ja"]["native"] == "日本語"
    assert by_code["en"]["name"] == "English"


# ---------------------------------------------------------------------------- add


def test_add_supported_language_starts_with_empty_presets(client: TestClient) -> None:
    response = client.post("/api/languages", json={"code": "fr"})
    assert response.status_code == 201, response.text
    assert response.json()["code"] == "fr"
    assert response.json()["presets"] == 0
    assert response.json()["supported"] is True
    assert "fr" in codes(client)
    assert client.get("/api/languages/fr/presets").json() == {"presets": []}


def test_add_unsupported_language_is_400(client: TestClient) -> None:
    response = client.post("/api/languages", json={"code": "xx"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_language"
    assert "xx" not in codes(client)


def test_add_already_enabled_language_is_409(client: TestClient) -> None:
    response = client.post("/api/languages", json={"code": "en"})
    assert response.status_code == 409
    assert codes(client) == ["en", "ja", "ko", "zh"]


# ------------------------------------------------------------------- set default


def test_set_default(client: TestClient) -> None:
    response = client.patch("/api/languages/ko", json={"default": True})
    assert response.status_code == 200, response.text
    assert response.json()["default"] is True
    languages = client.get("/api/languages").json()["languages"]
    assert languages[0]["code"] == "ko"
    assert sum(1 for item in languages if item["default"]) == 1


def test_set_default_unknown_language_is_404(client: TestClient) -> None:
    assert client.patch("/api/languages/xx", json={"default": True}).status_code == 404


# ------------------------------------------------------------------------ remove


def test_remove_default_language_is_409(client: TestClient) -> None:
    response = client.delete("/api/languages/en")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "language_locked"


def test_remove_last_language_is_409(client: TestClient) -> None:
    for code in ("ja", "ko", "zh"):
        assert client.delete(f"/api/languages/{code}").status_code == 200
    assert codes(client) == ["en"]
    response = client.delete("/api/languages/en")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "language_locked"


def test_remove_unused_language(client: TestClient) -> None:
    response = client.delete("/api/languages/ko")
    assert response.status_code == 200, response.text
    assert response.json() == {"removed": "ko", "voices": None}
    assert codes(client) == ["en", "ja", "zh"]


def test_remove_unknown_language_is_404(client: TestClient) -> None:
    assert client.delete("/api/languages/xx").status_code == 404


def test_remove_with_voices_needs_a_decision(client: TestClient, store: VoiceStore) -> None:
    make_voice(store, "mia-zh", "zh")
    response = client.delete("/api/languages/zh")
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "language_in_use"
    assert "Mia Zh" in error["message"] or "mia-zh" in error["message"]
    assert store.exists("mia-zh")


def test_remove_relabels_voices_and_backs_up(
    client: TestClient, settings: Settings, store: VoiceStore
) -> None:
    make_voice(store, "mia-zh", "zh")
    response = client.request("DELETE", "/api/languages/zh", json={"voices": "relabel", "to": "ja"})
    assert response.status_code == 200, response.text
    assert response.json() == {"removed": "zh", "voices": "relabel"}
    assert store.get("mia-zh").language == "ja"
    assert "zh" not in codes(client)
    assert sorted(settings.backups_dir.glob("languages-*.json")), "languages.json not backed up"
    assert sorted(settings.backups_dir.glob("voices-*language-remove-zh*.zip")), (
        "voices/ not backed up"
    )


def test_remove_relabel_target_must_be_enabled(client: TestClient, store: VoiceStore) -> None:
    make_voice(store, "mia-zh", "zh")
    response = client.request("DELETE", "/api/languages/zh", json={"voices": "relabel", "to": "de"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "language_not_enabled"
    assert store.get("mia-zh").language == "zh"
    response = client.request("DELETE", "/api/languages/zh", json={"voices": "relabel", "to": "zh"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "language_not_enabled"


def test_remove_deletes_voices(client: TestClient, settings: Settings, store: VoiceStore) -> None:
    make_voice(store, "mia-zh", "zh")
    make_voice(store, "other-zh", "zh")
    response = client.request("DELETE", "/api/languages/zh", json={"voices": "delete"})
    assert response.status_code == 200, response.text
    assert response.json() == {"removed": "zh", "voices": "delete"}
    assert not store.exists("mia-zh") and not store.exists("other-zh")
    assert sorted(settings.backups_dir.glob("voices-*language-remove-zh*.zip"))


def test_remove_drops_presets(client: TestClient, settings: Settings) -> None:
    client.put(
        "/api/languages/ko/presets", json={"presets": [{"id": "a", "label": "A", "text": "x"}]}
    )
    assert client.delete("/api/languages/ko").status_code == 200
    data = json.loads((settings.data_dir / "languages.json").read_text("utf-8"))
    assert "ko" not in data["presets"]


# ----------------------------------------------------------------------- presets


def test_presets_roundtrip(client: TestClient) -> None:
    items = [
        {"id": "greeting", "label": "Greeting", "text": "Hello there."},
        {"id": "status", "label": "Status", "text": "All done."},
    ]
    assert client.put("/api/languages/en/presets", json={"presets": items}).json() == {
        "presets": items
    }
    assert client.get("/api/languages/en/presets").json() == {"presets": items}
    row = next(
        item for item in client.get("/api/languages").json()["languages"] if item["code"] == "en"
    )
    assert row["presets"] == 2


@pytest.mark.parametrize(
    "items",
    [
        [{"id": "", "label": "A", "text": "x"}],
        [{"id": "a", "label": "", "text": "x"}],
        [{"id": "a", "label": "A", "text": "  "}],
        [{"id": "a", "label": "A", "text": "x"}, {"id": "a", "label": "B", "text": "y"}],
        [{"id": f"p{i}", "label": f"P{i}", "text": "x"} for i in range(13)],
    ],
)
def test_invalid_presets_are_400(client: TestClient, items: list[dict[str, str]]) -> None:
    response = client.put("/api/languages/en/presets", json={"presets": items})
    assert response.status_code == 400, response.text
    assert client.get("/api/languages/en/presets").json()["presets"]  # unchanged


def test_presets_unknown_language_is_404(client: TestClient) -> None:
    assert client.get("/api/languages/xx/presets").status_code == 404
    assert client.put("/api/languages/xx/presets", json={"presets": []}).status_code == 404


# -------------------------------------------------------------------------- drift


def test_drift_keeps_language_enabled_but_marks_unsupported(
    settings: Settings, store: VoiceStore, tmp_path: Path
) -> None:
    raw = json.loads(settings.supported_languages_file.read_text("utf-8"))
    raw["languages"] = [item for item in raw["languages"] if item["code"] != "ko"]
    drifted = tmp_path / "supported-languages.json"
    drifted.write_text(json.dumps(raw), "utf-8")
    settings.supported_languages_file = drifted

    with TestClient(create_app(settings)) as client:
        rows = {item["code"]: item for item in client.get("/api/languages").json()["languages"]}
        assert "ko" in rows and rows["ko"]["supported"] is False
        assert rows["en"]["supported"] is True
        assert client.get("/api/languages/supported").status_code == 200
        assert client.post("/api/languages", json={"code": "ko"}).status_code == 400
        make_voice(store, "ko-voice", "ko")  # still usable: enabled, just not supported


def test_missing_supported_file_fails_loudly(settings: Settings, store: VoiceStore) -> None:
    settings.supported_languages_file = settings.data_dir / "nope.json"
    with pytest.raises(RuntimeError):
        LanguageStore(settings, store)


# ------------------------------------------------------- voice-language enforcement


def test_clone_with_disabled_language_is_400(client: TestClient, store: VoiceStore) -> None:
    files = {
        "file": ("ref.wav", io.BytesIO(audio.wav_bytes(tone(6.0), RATE)), "audio/wav"),
        "name": (None, "de-voice"),
        "language": (None, "de"),
        "ref_text": (None, "hallo"),
        "consent": (None, "true"),
    }
    response = client.post("/api/voices/clone", files=files)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "language_not_enabled"
    assert not store.exists("de-voice")


def test_patch_voice_with_disabled_language_is_400(client: TestClient, store: VoiceStore) -> None:
    make_voice(store, "ashley-en", "en")
    response = client.patch("/api/voices/ashley-en", json={"language": "de"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "language_not_enabled"
    assert store.get("ashley-en").language == "en"


def test_save_as_voice_with_disabled_language_is_400(client: TestClient, store: VoiceStore) -> None:
    make_voice(store, "ashley-en", "en")
    take = client.post("/api/speak", json={"model": "voxcpm2", "voice": "ashley-en", "input": "hi"})
    assert take.status_code == 200, take.text
    response = client.post(
        f"/api/takes/{take.json()['id']}/save-as-voice", json={"name": "de-take", "language": "de"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "language_not_enabled"
    assert not store.exists("de-take")


def test_design_save_with_disabled_language_is_400(client: TestClient, store: VoiceStore) -> None:
    candidate = client.post(
        "/api/design/candidates",
        json={"description": "warm", "sample_text": "hello", "language": "en", "n": 1},
    ).json()["candidates"][0]
    response = client.post(
        "/api/design/save",
        json={"candidate_id": candidate["id"], "name": "de-designed", "language": "de"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "language_not_enabled"
    assert not store.exists("de-designed")
