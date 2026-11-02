"""Voice/take store: file format, index, backups, retention."""

from __future__ import annotations

import zipfile

import numpy as np
import pytest
from app.config import Settings
from app.errors import BadRequest, Conflicting
from app.store import VoiceStore


def tone(seconds: float = 1.0, rate: int = 24000) -> np.ndarray:
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def test_create_writes_qwen3_compatible_metadata(store: VoiceStore, make_voice):
    voice = make_voice("Ashley-EN", "en")
    assert voice.key == "ashley-en"
    assert voice.metadata["voice_name_lower"] == "ashley-en"
    assert voice.metadata["name"] == "Ashley-EN"
    assert voice.metadata["embedding_source"] == "audio"
    assert voice.metadata["consent"] == "uploaded-via-studio"
    assert voice.metadata["language"] == "en"
    assert voice.metadata["source"] == "uploaded"
    assert int(voice.metadata["sample_rate"]) == 24000
    assert voice.metadata["ref_text"].startswith("Thanks for waiting")
    assert voice.duration_s == pytest.approx(1.0, abs=0.01)


def test_list_uses_creation_order_and_survives_reload(store: VoiceStore, make_voice, settings):
    make_voice("ashley-en", "en")
    make_voice("ashley-ja", "ja")
    assert [v.key for v in store.list()] == ["ashley-en", "ashley-ja"]

    reloaded = VoiceStore(settings)
    assert [v.key for v in reloaded.list()] == ["ashley-en", "ashley-ja"]
    assert reloaded.get("ASHLEY-JA").language == "ja"


def test_data_uri_is_cached_and_invalidated_on_update(store: VoiceStore, make_voice):
    make_voice("ashley-en", "en")
    first = store.data_uri("ashley-en")
    assert first.startswith("data:audio/wav;base64,")
    assert store.data_uri("ashley-en") == first

    store.update("ashley-en", ref_text="new transcript")
    store.data_uri("ashley-en")  # still readable after rewrite


def test_duplicate_and_invalid_names_are_rejected(store: VoiceStore, make_voice):
    make_voice("ashley-en", "en")
    with pytest.raises(Conflicting):
        store.create(
            "ashley-en",
            tone(),
            24000,
            ref_text="x",
            language="en",
            consent="uploaded-via-studio",
        )
    with pytest.raises(BadRequest):
        store.create(
            "../escape",
            tone(),
            24000,
            ref_text="x",
            language="en",
            consent="uploaded-via-studio",
        )


def test_rename_moves_file_and_index(store: VoiceStore, make_voice):
    make_voice("ashley-en", "en")
    renamed = store.update("ashley-en", new_name="ashley-us", language="en")
    assert renamed.key == "ashley-us"
    assert not (store.root / "ashley-en.safetensors").exists()
    assert (store.root / "ashley-us.safetensors").exists()
    assert [v.key for v in store.list()] == ["ashley-us"]


def test_delete_writes_backup(store: VoiceStore, make_voice):
    make_voice("ashley-en", "en")
    store.delete("ashley-en")
    assert not (store.root / "ashley-en.safetensors").exists()
    backups = list(store.settings.backups_dir.glob("voices-*.zip"))
    assert backups
    with zipfile.ZipFile(backups[-1]) as zf:
        assert "voices/ashley-en.safetensors" in zf.namelist()


def test_take_store_roundtrip_and_wave(settings: Settings):
    from app.store import TakeStore

    takes = TakeStore(settings)
    meta = takes.create(tone(0.5), 24000, voice="ashley-en", text="hello", language="en")
    assert meta["duration_s"] == pytest.approx(0.5, abs=0.01)
    assert takes.get(meta["id"])["text"] == "hello"
    amps = takes.wave(meta["id"])
    assert len(amps) == 72
    assert takes.list()[0]["id"] == meta["id"]
    takes.delete(meta["id"])
    assert takes.list() == []


def test_take_retention_keeps_newest(tmp_path):
    from app.store import TakeStore

    settings = Settings(data_dir=tmp_path / "data", take_retention=2)
    takes = TakeStore(settings)
    ids = [takes.create(tone(0.1), 24000, voice="v", text=f"take {i}")["id"] for i in range(4)]
    remaining = {item["id"] for item in takes.list(limit=100)}
    assert remaining == set(ids[-2:])


def test_settings_file_is_created(store: VoiceStore):
    assert store.settings.settings_file.exists()
