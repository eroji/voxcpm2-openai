"""Disk store for voices, takes, backups and settings (SPEC §5).

Voice files are safetensors: a float32 mono ``audio`` tensor plus string metadata.
The on-disk format is byte-compatible with the existing Qwen3 speaker files.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from safetensors import safe_open
from safetensors.numpy import load_file, save_file

from app import audio as audio_utils
from app.config import Settings
from app.errors import BadRequest, Conflicting, NotFound
from app.models import VOICE_NAME_PATTERN

NAME_RE = re.compile(VOICE_NAME_PATTERN)
TAKE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,80}$")
INDEX_VERSION = 1
Envelope = list[float]
DEFAULT_SETTINGS = {
    "cfg": 2.0,
    "steps": 10,
    "format": "wav",
    "stream": False,
    "take_retention": 50,
}


class VoiceNotFound(NotFound):
    def __init__(self, name: str, valid: list[str] | None = None):
        suffix = f" Valid voices: {', '.join(valid)}" if valid else ""
        super().__init__(f"Unknown voice '{name}'.{suffix}", code="voice_not_found")


class TakeNotFound(NotFound):
    def __init__(self, take_id: str):
        super().__init__(f"Unknown take '{take_id}'.", code="take_not_found")


def validate_voice_name(name: str) -> str:
    candidate = name.strip()
    if not candidate or not NAME_RE.match(candidate.lower()):
        raise BadRequest(
            "Voice names must match [a-z0-9][a-z0-9_-]{0,63}.",
            param="name",
            code="invalid_voice_name",
        )
    return candidate.lower()


@dataclass
class Voice:
    key: str
    name: str
    ref_text: str
    language: str
    source: str
    consent: str
    sample_rate: int
    created_at: int
    duration_s: float
    path: Path
    metadata: dict[str, str] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "language": self.language,
            "source": self.source,
            "ref_duration_s": round(self.duration_s, 2),
            "ref_text": self.ref_text,
            "created_at": self.created_at,
        }


def _now() -> int:
    return int(time.time())


def _read_voice_file(path: Path) -> Voice:
    with safe_open(str(path), framework="numpy") as handle:
        meta = {str(k): str(v) for k, v in (handle.metadata() or {}).items()}
        tensor = handle.get_tensor("audio")
    sample_rate = int(meta.get("sample_rate") or 0)
    samples = np.asarray(tensor, dtype=np.float32)
    key = meta.get("voice_name_lower") or path.stem
    return Voice(
        key=key,
        name=meta.get("name") or key,
        ref_text=meta.get("ref_text", ""),
        language=meta.get("language", ""),
        source=meta.get("source", "uploaded"),
        consent=meta.get("consent", ""),
        sample_rate=sample_rate,
        created_at=int(meta.get("created_at") or 0),
        duration_s=(len(samples) / sample_rate) if sample_rate else 0.0,
        path=path,
        metadata=meta,
    )


class VoiceStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.voices_dir
        self.index_path = settings.data_dir / "voices.json"
        self.root.mkdir(parents=True, exist_ok=True)
        settings.backups_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._uri_cache: dict[str, tuple[int, str]] = {}
        self._index = self._load_index()
        self.ensure_settings_file()

    # ---------------------------------------------------------------- index
    def _load_index(self) -> dict[str, Any]:
        if self.index_path.exists():
            try:
                data = json.loads(self.index_path.read_text("utf-8"))
                if isinstance(data, dict) and data.get("version") == INDEX_VERSION:
                    data.setdefault("order", [])
                    data.setdefault("voices", {})
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return self._rebuild_index()

    def _rebuild_index(self) -> dict[str, Any]:
        voices: dict[str, Any] = {}
        order: list[str] = []
        for path in sorted(self.root.glob("*.safetensors")):
            try:
                voice = _read_voice_file(path)
            except Exception:  # noqa: BLE001 - a corrupt file must not break the library
                continue
            voices[voice.key] = {
                "name": voice.name,
                "language": voice.language,
                "source": voice.source,
                "duration_s": round(voice.duration_s, 3),
                "created_at": voice.created_at,
            }
            order.append(voice.key)
        index = {"version": INDEX_VERSION, "order": order, "voices": voices}
        self._write_index(index)
        return index

    def _write_index(self, index: dict[str, Any] | None = None) -> None:
        index = index if index is not None else self._index
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(self.index_path)

    def _touch_index(self, voice: Voice) -> None:
        with self._lock:
            if voice.key not in self._index["order"]:
                self._index["order"].append(voice.key)
            self._index["voices"][voice.key] = {
                "name": voice.name,
                "language": voice.language,
                "source": voice.source,
                "duration_s": round(voice.duration_s, 3),
                "created_at": voice.created_at,
            }
            self._write_index()

    def _drop_from_index(self, key: str) -> None:
        with self._lock:
            self._index["order"] = [k for k in self._index["order"] if k != key]
            self._index["voices"].pop(key, None)
            self._write_index()

    # ----------------------------------------------------------------- read
    def exists(self, name: str) -> bool:
        try:
            key = validate_voice_name(name)
        except BadRequest:
            return False
        return (self.root / f"{key}.safetensors").exists()

    def get(self, name: str) -> Voice:
        key = validate_voice_name(name)
        path = self.root / f"{key}.safetensors"
        if not path.exists():
            raise VoiceNotFound(name, [v.name for v in self.list()])
        with self._lock:
            return _read_voice_file(path)

    def list(self) -> list[Voice]:
        with self._lock:
            order = list(self._index["order"])
            voices: list[Voice] = []
            seen: set[str] = set()
            for key in order:
                path = self.root / f"{key}.safetensors"
                if not path.exists():
                    continue
                try:
                    voice = _read_voice_file(path)
                except Exception:  # noqa: BLE001
                    continue
                voices.append(voice)
                seen.add(key)
            for path in sorted(self.root.glob("*.safetensors")):
                if path.stem in seen:
                    continue
                try:
                    voices.append(_read_voice_file(path))
                except Exception:  # noqa: BLE001
                    continue
            return voices

    def read_audio(self, name: str) -> tuple[np.ndarray, int]:
        voice = self.get(name)
        with safe_open(str(voice.path), framework="numpy") as handle:
            samples = np.asarray(handle.get_tensor("audio"), dtype=np.float32)
        return samples, voice.sample_rate

    def data_uri(self, name: str) -> str:
        voice = self.get(name)
        mtime = voice.path.stat().st_mtime_ns
        with self._lock:
            cached = self._uri_cache.get(voice.key)
            if cached and cached[0] == mtime:
                return cached[1]
        uri = audio_utils.to_wav_data_uri(*self.read_audio(voice.key))
        with self._lock:
            self._uri_cache[voice.key] = (mtime, uri)
        return uri

    # ---------------------------------------------------------------- write
    def create(
        self,
        name: str,
        samples: np.ndarray,
        sample_rate: int,
        *,
        ref_text: str,
        language: str,
        consent: str,
        source: str = "uploaded",
        metadata: dict[str, str] | None = None,
        overwrite: bool = False,
    ) -> Voice:
        key = validate_voice_name(name)
        path = self.root / f"{key}.safetensors"
        with self._lock:
            if path.exists() and not overwrite:
                raise Conflicting(f"Voice '{key}' already exists.")
            if path.exists():
                self.backup(f"overwrite-{key}")
            now = _now()
            samples = np.ascontiguousarray(samples, dtype=np.float32)
            meta: dict[str, str] = dict(metadata or {})
            meta.update(
                {
                    "name": name.strip(),
                    "voice_name_lower": key,
                    "ref_text": ref_text or "",
                    "sample_rate": str(sample_rate),
                    "created_at": str(now),
                    "consent": consent,
                    "embedding_source": "audio",
                    "language": language or "",
                    "source": source,
                    "mime_type": meta.get("mime_type", "audio/wav"),
                    "original_filename": meta.get("original_filename", f"{key}.wav"),
                    "file_size": str(len(audio_utils.wav_bytes(samples, sample_rate))),
                }
            )
            meta = {str(k): str(v) for k, v in meta.items() if v is not None}
            tmp = path.with_suffix(".safetensors.tmp")
            save_file({"audio": samples}, str(tmp), metadata=meta)
            tmp.replace(path)
            voice = _read_voice_file(path)
            self._touch_index(voice)
            self._uri_cache.pop(key, None)
            return voice

    def update(
        self,
        name: str,
        *,
        new_name: str | None = None,
        ref_text: str | None = None,
        language: str | None = None,
    ) -> Voice:
        with self._lock:
            voice = self.get(name)
            self.backup(f"update-{voice.key}")
            samples = load_file(str(voice.path))["audio"]
            meta = dict(voice.metadata)
            old_key = voice.key
            key = old_key
            if new_name is not None:
                key = validate_voice_name(new_name)
                if key != old_key and (self.root / f"{key}.safetensors").exists():
                    raise Conflicting(f"Voice '{key}' already exists.")
                meta["name"] = new_name.strip()
                meta["voice_name_lower"] = key
            if ref_text is not None:
                meta["ref_text"] = ref_text
            if language is not None:
                meta["language"] = language
            new_path = self.root / f"{key}.safetensors"
            tmp = new_path.with_suffix(".safetensors.tmp")
            save_file({"audio": np.ascontiguousarray(samples)}, str(tmp), metadata=meta)
            tmp.replace(new_path)
            if key != old_key:
                voice.path.unlink(missing_ok=True)
                self._drop_from_index(old_key)
            updated = _read_voice_file(new_path)
            self._touch_index(updated)
            self._uri_cache.pop(old_key, None)
            self._uri_cache.pop(key, None)
            return updated

    def delete(self, name: str) -> None:
        with self._lock:
            voice = self.get(name)
            self.backup(f"delete-{voice.key}")
            voice.path.unlink(missing_ok=True)
            self._drop_from_index(voice.key)
            self._uri_cache.pop(voice.key, None)

    # --------------------------------------------------------------- backup
    def backup(self, reason: str) -> Path:
        self.settings.backups_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        target = self.settings.backups_dir / f"voices-{stamp}-{reason}.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(self.root.glob("*.safetensors")):
                zf.write(path, arcname=f"voices/{path.name}")
            if self.index_path.exists():
                zf.write(self.index_path, arcname="voices.json")
        backups = sorted(self.settings.backups_dir.glob("voices-*.zip"))
        keep = max(1, self.settings.backup_retention)
        for old in backups[:-keep]:
            old.unlink(missing_ok=True)
        return target

    # -------------------------------------------------------------- settings
    def ensure_settings_file(self) -> None:
        path = self.settings.settings_file
        if not path.exists():
            payload = dict(DEFAULT_SETTINGS)
            payload["take_retention"] = self.settings.take_retention
            path.write_text(json.dumps(payload, indent=2), "utf-8")


class TakeStore:
    """Studio takes: ``<id>.wav`` + ``<id>.json`` under ``DATA_DIR/takes``."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.takes_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._wave_cache: dict[str, tuple[int, list[float]]] = {}

    def _paths(self, take_id: str) -> tuple[Path, Path]:
        if not TAKE_ID_RE.match(take_id):
            raise TakeNotFound(take_id)
        return self.root / f"{take_id}.wav", self.root / f"{take_id}.json"

    def _validate_id(self, take_id: str) -> str:
        if not TAKE_ID_RE.match(take_id):
            raise TakeNotFound(take_id)
        return take_id

    def new_id(self) -> str:
        return f"{int(time.time() * 1000)}-{secrets.token_hex(3)}"

    def create(
        self,
        samples: np.ndarray,
        sample_rate: int,
        *,
        voice: str,
        text: str,
        language: str = "",
        kind: str = "studio",
        rtf: float | None = None,
        first_audio_ms: float | None = None,
        extra: dict[str, Any] | None = None,
        take_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            take_id = self._validate_id(take_id) if take_id else self.new_id()
            wav_path, json_path = self._paths(take_id)
            audio_utils.write_wav(wav_path, samples, sample_rate)
            meta = {
                "id": take_id,
                "voice": voice,
                "text": text,
                "language": language,
                "kind": kind,
                "duration_s": round(audio_utils.duration_s(samples, sample_rate), 3),
                "rtf": round(rtf, 4) if rtf is not None else None,
                "first_audio_ms": round(first_audio_ms, 1) if first_audio_ms is not None else None,
                "sample_rate": sample_rate,
                "created_at": int(now),
            }
            if extra:
                meta.update(extra)
            json_path.write_text(json.dumps(meta, ensure_ascii=False), "utf-8")
            self.prune()
            return meta

    def get(self, take_id: str) -> dict[str, Any]:
        _, json_path = self._paths(take_id)
        if not json_path.exists():
            raise TakeNotFound(take_id)
        try:
            return json.loads(json_path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise TakeNotFound(take_id) from exc

    def list(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in self.root.glob("*.json"):
            try:
                items.append(json.loads(path.read_text("utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        items.sort(key=lambda item: (item.get("created_at", 0), item.get("id", "")), reverse=True)
        return items[: limit or self.settings.take_retention]

    def audio_path(self, take_id: str) -> Path:
        wav_path, json_path = self._paths(take_id)
        if not wav_path.exists() or not json_path.exists():
            raise TakeNotFound(take_id)
        return wav_path

    def wave(self, take_id: str) -> Envelope:
        path = self.audio_path(take_id)
        mtime = path.stat().st_mtime_ns
        with self._lock:
            cached = self._wave_cache.get(take_id)
            if cached and cached[0] == mtime:
                return cached[1]
        samples, _ = audio_utils.read_audio_file(path)
        amps = audio_utils.rms_envelope(samples, 72)
        with self._lock:
            self._wave_cache[take_id] = (mtime, amps)
        return amps

    def delete(self, take_id: str) -> None:
        wav_path, json_path = self._paths(take_id)
        if not json_path.exists():
            raise TakeNotFound(take_id)
        with self._lock:
            wav_path.unlink(missing_ok=True)
            json_path.unlink(missing_ok=True)
            self._wave_cache.pop(take_id, None)

    def prune(self) -> None:
        """Keep the newest ``take_retention`` takes (SPEC §7)."""
        items = self.list(limit=10**9)
        for item in items[self.settings.take_retention :]:
            take_id = item.get("id", "")
            if not TAKE_ID_RE.match(take_id):
                continue
            self.audio_path(take_id).unlink(missing_ok=True)
            self._paths(take_id)[1].unlink(missing_ok=True)
            self._wave_cache.pop(take_id, None)

    def take_urls(self, take_id: str) -> dict[str, str]:
        return {
            "audio_url": f"/api/takes/{take_id}/audio",
            "wave_url": f"/api/takes/{take_id}/wave",
        }
