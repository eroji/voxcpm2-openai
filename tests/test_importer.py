"""Qwen3 speaker importer: language/source mapping, skip/overwrite, failure isolation."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from app import store as app_store
from app.importer import import_qwen3_speakers
from app.store import VoiceStore
from safetensors.numpy import save_file

VALID_NAMES = [
    "alison-en",
    "ashley-en",
    "ashley-ja",
    "ashley-kr",
    "ashley-zh",
    "bennett-en",
    "jack",
]


def tone(seconds: float = 1.0, rate: int = 24000) -> np.ndarray:
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def speaker_file(
    src: Path,
    file_name: str | None = None,
    *,
    name: str | None = None,
    audio: np.ndarray | None = None,
    **overrides: str | None,
) -> Path:
    display = name or file_name or "voice"
    meta: dict[str, str] = {
        "name": display,
        "voice_name_lower": display.lower(),
        "ref_text": "Thanks for waiting. Everything checks out.",
        "sample_rate": "24000",
        "created_at": "1791831655",
        "consent": "uploaded-via-studio",
        "embedding_source": "audio",
        "mime_type": "audio/wav",
        "original_filename": f"{display}.wav",
        "file_size": "48044",
    }
    for key, value in overrides.items():
        if value is None:
            meta.pop(key, None)
        else:
            meta[key] = value
    path = src / f"{file_name or display}.safetensors"
    save_file({"audio": tone() if audio is None else audio}, str(path), metadata=meta)
    return path


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    src = tmp_path / "import"
    src.mkdir()
    for name in VALID_NAMES:
        speaker_file(src, name)
    speaker_file(src, "alison-en", consent="own-generated-voice")  # designed
    speaker_file(src, "bennett-en", consent=None)  # consent missing -> default
    speaker_file(src, "Bad Name", voice_name_lower="Bad Name")  # invalid name
    (src / "broken.safetensors").write_bytes(b"definitely not safetensors")
    return src


def test_import_maps_language_and_source(store: VoiceStore, source_dir: Path):
    result = import_qwen3_speakers(store, source_dir)
    assert sorted(result["imported"]) == VALID_NAMES
    assert result["skipped"] == []
    assert store.get("ashley-en").language == "en"
    assert store.get("ashley-ja").language == "ja"
    assert store.get("ashley-kr").language == "ko"
    assert store.get("ashley-zh").language == "zh"
    assert store.get("jack").language == ""
    assert store.get("alison-en").source == "designed"
    assert store.get("alison-en").consent == "own-generated-voice"
    assert store.get("ashley-en").source == "uploaded"
    assert store.get("bennett-en").source == "uploaded"
    assert store.get("bennett-en").consent == "uploaded-via-studio"


def test_import_preserves_audio_and_metadata(store: VoiceStore, source_dir: Path):
    import_qwen3_speakers(store, source_dir)
    samples, rate = store.read_audio("ashley-en")
    assert rate == 24000
    assert np.allclose(samples, tone())
    voice = store.get("ashley-en")
    assert voice.ref_text.startswith("Thanks for waiting")
    assert voice.metadata["mime_type"] == "audio/wav"
    assert voice.metadata["original_filename"] == "ashley-en.wav"
    assert voice.metadata["created_at"] == "1791831655"


def test_import_is_lossless_for_values_code_cannot_derive(store: VoiceStore, tmp_path: Path):
    """file_size/created_at/name come from the source file, never recomputed."""
    src = tmp_path / "lossless"
    src.mkdir()
    speaker_file(src, "Ashley-EN", file_size="12345", created_at="1700000000")
    assert import_qwen3_speakers(store, src)["imported"] == ["Ashley-EN"]
    voice = store.get("ashley-en")
    assert voice.key == "ashley-en"
    assert voice.name == "Ashley-EN"
    assert voice.metadata["name"] == "Ashley-EN"
    assert voice.metadata["voice_name_lower"] == "ashley-en"
    assert voice.metadata["file_size"] == "12345"
    assert voice.metadata["created_at"] == "1700000000"
    assert voice.created_at == 1700000000


def test_corrupt_and_invalid_names_go_to_failed(store: VoiceStore, source_dir: Path):
    result = import_qwen3_speakers(store, source_dir)
    failed = dict(result["failed"])
    assert set(failed) == {"broken", "Bad Name"}
    assert all(message for message in failed.values())
    assert not store.exists("broken")
    assert not store.exists("Bad Name")


def test_bad_tensors_and_paths_fail_without_aborting(
    store: VoiceStore, source_dir: Path, tmp_path: Path
):
    speaker_file(source_dir, "stereo-en", audio=np.zeros((24000, 2), dtype=np.float32))
    speaker_file(source_dir, "empty-en", audio=np.zeros(0, dtype=np.float32))
    speaker_file(source_dir, "evil", name="../evil", voice_name_lower="../evil")
    result = import_qwen3_speakers(store, source_dir)
    failed = dict(result["failed"])
    assert set(failed) == {"broken", "Bad Name", "stereo-en", "empty-en", "../evil"}
    assert all(message for message in failed.values())
    assert sorted(result["imported"]) == VALID_NAMES
    assert not store.exists("stereo-en")
    assert not store.exists("empty-en")
    assert sorted(p.name for p in store.root.glob("*.safetensors")) == [
        f"{name.lower()}.safetensors" for name in VALID_NAMES
    ]
    assert list(store.settings.data_dir.rglob("*evil*")) == []


def test_second_run_skips_existing(store: VoiceStore, source_dir: Path):
    first = import_qwen3_speakers(store, source_dir)
    result = import_qwen3_speakers(store, source_dir)
    assert result["imported"] == []
    assert sorted(result["skipped"]) == sorted(first["imported"])


def test_overwrite_replaces_existing(store: VoiceStore, source_dir: Path, make_voice):
    make_voice("ashley-en", "en", seconds=0.5)
    result = import_qwen3_speakers(store, source_dir, overwrite=True)
    assert "ashley-en" in result["imported"]
    assert result["skipped"] == []
    samples, _ = store.read_audio("ashley-en")
    assert np.allclose(samples, tone())
    assert len(store.list()) == len(VALID_NAMES)


def test_missing_or_empty_source_dir_is_not_an_error(store: VoiceStore, tmp_path: Path):
    empty_result: dict = {"imported": [], "skipped": [], "failed": []}
    assert import_qwen3_speakers(store, tmp_path / "nope") == empty_result
    empty = tmp_path / "empty"
    empty.mkdir()
    assert import_qwen3_speakers(store, empty) == empty_result


def test_name_key_mismatch_imports_under_key(store: VoiceStore, tmp_path: Path):
    """SPEC §5: the key is voice_name_lower, not the display name."""
    src = tmp_path / "mismatch"
    src.mkdir()
    speaker_file(src, "ashley-en", name="Ashley EN", voice_name_lower="ashley-en")
    result = import_qwen3_speakers(store, src)
    assert result["imported"] == ["Ashley EN"]
    assert result["failed"] == []
    assert (store.root / "ashley-en.safetensors").exists()
    voice = store.get("ashley-en")
    assert voice.name == "Ashley EN"
    assert voice.metadata["name"] == "Ashley EN"
    assert voice.language == "en"  # inferred from the key, not the display name


def test_import_preserves_file_size_without_reencoding(
    store: VoiceStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    src = tmp_path / "sizes"
    src.mkdir()
    speaker_file(src, "ashley-en", file_size="48044")

    def boom(*args: object, **kwargs: object) -> bytes:
        raise AssertionError("wav_bytes must not run when file_size is present")

    monkeypatch.setattr(app_store.audio_utils, "wav_bytes", boom)
    assert import_qwen3_speakers(store, src)["imported"] == ["ashley-en"]
    assert store.get("ashley-en").metadata["file_size"] == "48044"
