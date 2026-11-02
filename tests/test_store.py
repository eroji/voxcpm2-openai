"""Voice/take store: file format, index, backups, retention."""

from __future__ import annotations

import json
import time
import zipfile

import numpy as np
import pytest
from app import audio as audio_utils
from app.config import Settings
from app.errors import BadRequest, Conflicting, NotFound
from app.store import VoiceStore
from safetensors.numpy import save_file


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


def test_create_keeps_caller_metadata_and_defaults_the_rest(store: VoiceStore):
    voice = store.create(
        "Ashley-EN",
        tone(),
        24000,
        ref_text="x",
        language="en",
        consent="uploaded-via-studio",
        metadata={"created_at": "1700000000", "file_size": "12345", "mime_type": "audio/flac"},
    )
    assert voice.metadata["created_at"] == "1700000000"
    assert voice.metadata["file_size"] == "12345"
    assert voice.metadata["mime_type"] == "audio/flac"
    assert voice.metadata["name"] == "Ashley-EN"
    assert voice.metadata["voice_name_lower"] == "ashley-en"
    assert voice.created_at == 1700000000

    plain = store.create(
        "plain-en", tone(), 24000, ref_text="x", language="en", consent="uploaded-via-studio"
    )
    assert plain.metadata["file_size"] == str(len(audio_utils.wav_bytes(tone(), 24000)))
    assert abs(int(plain.metadata["created_at"]) - time.time()) < 60


@pytest.mark.parametrize(
    "bad",
    [np.zeros((24000, 2), dtype=np.float32), np.zeros(0, dtype=np.float32)],
    ids=["stereo", "empty"],
)
def test_create_rejects_non_1d_or_empty_audio(store: VoiceStore, bad: np.ndarray):
    with pytest.raises(BadRequest) as exc:
        store.create(
            "bad-en", bad, 24000, ref_text="x", language="en", consent="uploaded-via-studio"
        )
    assert exc.value.code == "invalid_audio"
    assert not (store.root / "bad-en.safetensors").exists()


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


def test_create_accepts_explicit_key(store: VoiceStore):
    voice = store.create(
        "Ashley EN",
        tone(),
        24000,
        ref_text="x",
        language="en",
        consent="uploaded-via-studio",
        key="ashley-en",
    )
    assert voice.key == "ashley-en"
    assert voice.name == "Ashley EN"
    assert (store.root / "ashley-en.safetensors").exists()
    assert voice.metadata["voice_name_lower"] == "ashley-en"
    assert voice.metadata["name"] == "Ashley EN"
    with pytest.raises(BadRequest):
        store.create(
            "anything",
            tone(),
            24000,
            ref_text="x",
            language="en",
            consent="uploaded-via-studio",
            key="../escape",
        )


def test_display_name_voice_is_addressable_by_key(store: VoiceStore):
    """SPEC §5: the lowercase key is the lookup path; the display name is display-only."""
    voice = store.create(
        "Ashley EN",
        tone(),
        24000,
        ref_text="x",
        language="en",
        consent="uploaded-via-studio",
        key="ashley-en",
    )
    summary = voice.summary()
    assert summary["key"] == "ashley-en"
    assert summary["name"] == "Ashley EN"
    assert store.get("ashley-en").name == "Ashley EN"
    assert store.exists("ashley-en")
    samples, rate = store.read_audio("ashley-en")
    assert samples.size and rate == 24000
    assert store.data_uri("ashley-en").startswith("data:audio/wav;base64,")
    assert [v.key for v in store.list()] == ["ashley-en"]
    assert VoiceStore(store.settings).get("ashley-en").name == "Ashley EN"


def test_uppercase_hand_placed_file_is_invisible(store: VoiceStore, settings: Settings):
    """A file whose stem is not the lowercase key can never be fetched: skip it."""
    save_file(
        {"audio": tone()},
        str(store.root / "Ashley-en.safetensors"),
        metadata={"name": "Ashley-en", "sample_rate": "24000", "language": "en"},
    )
    assert store.list() == []
    for name in ("Ashley-en", "ashley-en", "ASHLEY-EN"):
        with pytest.raises(NotFound):
            store.get(name)
    assert VoiceStore(settings).list() == []  # index rebuild skips it too


def test_wave_cache_is_bounded(tmp_path):
    from app.store import TakeStore

    settings = Settings(data_dir=tmp_path / "data", take_retention=100)
    takes = TakeStore(settings)
    for i in range(70):
        meta = takes.create(tone(0.1), 24000, voice="v", text=f"take {i}")
        takes.wave(meta["id"])
    assert len(takes._wave_cache) <= 64


def test_hand_placed_metadata_key_cannot_escape(store: VoiceStore):
    """SPEC §5: the filename is the key; metadata voice_name_lower is untrusted."""
    save_file(
        {"audio": tone()},
        str(store.root / "escape.safetensors"),
        metadata={
            "name": "Escape",
            "voice_name_lower": "../../escape",
            "ref_text": "x",
            "sample_rate": "24000",
            "created_at": "1700000000",
            "consent": "uploaded-via-studio",
            "language": "en",
            "source": "uploaded",
        },
    )
    assert store.get("escape").key == "escape"
    updated = store.update("escape", ref_text="edited")
    assert updated.metadata["voice_name_lower"] == "escape"  # rewritten on write
    store.backup("delete-../../escape")
    store.delete("escape")
    assert not (store.root / "escape.safetensors").exists()
    assert not (store.settings.data_dir.parent / "escape.zip").exists()
    assert all(
        p.parent == store.settings.backups_dir for p in store.settings.data_dir.rglob("*.zip")
    )


def test_backup_sanitizes_reason(store: VoiceStore, make_voice):
    make_voice("ashley-en", "en")
    target = store.backup("../../evil")
    assert target.parent == store.settings.backups_dir
    assert target.exists()


def test_take_prune_survives_missing_wav_and_bad_ids(tmp_path):
    from app.store import TakeStore

    settings = Settings(data_dir=tmp_path / "data", take_retention=1)
    takes = TakeStore(settings)
    first = takes.create(tone(0.1), 24000, voice="v", text="one")["id"]
    (takes.root / "orphan1.json").write_text(
        json.dumps({"id": "orphan1", "created_at": 0}), "utf-8"
    )
    (takes.root / "junk.json").write_text(json.dumps({"id": "!!", "created_at": 0}), "utf-8")
    second = takes.create(tone(0.1), 24000, voice="v", text="two")["id"]
    assert takes.get(second)["text"] == "two"
    assert not (takes.root / f"{first}.json").exists()
    assert not (takes.root / "orphan1.json").exists()  # json removed despite missing wav
    assert (takes.root / "junk.json").exists()  # malformed id skipped


@pytest.mark.parametrize("text", ["こんにちは、世界", "안녕하세요 세계", "你好，世界"])
def test_non_ascii_ref_text_roundtrip(store: VoiceStore, settings: Settings, text: str):
    store.create(
        "multi-lang",
        tone(),
        24000,
        ref_text=text,
        language="ja",
        consent="uploaded-via-studio",
    )
    assert store.get("multi-lang").ref_text == text
    assert store.list()[0].ref_text == text
    json.loads(store.index_path.read_text("utf-8"))
    assert VoiceStore(settings).get("multi-lang").ref_text == text
