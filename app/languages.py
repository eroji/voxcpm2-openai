"""App-level languages (SPEC §5a): the engine's supported set plus the Studio's enabled set.

VoxCPM2 detects the language from the text and takes no language tag, so a language is only
an app-level label: the tag on a voice, the Voices filters, the Clone/Design choices and the
test phrases. The supported set comes from ``deploy/supported-languages.json`` (swapping models
means swapping the file); the enabled set lives in ``DATA_DIR/languages.json``.
"""

from __future__ import annotations

import json
import logging
import secrets
import shutil
import threading
from datetime import UTC, datetime
from typing import Any

from app.config import Settings
from app.errors import APIError, BadRequest, NotFound
from app.store import VoiceStore

logger = logging.getLogger(__name__)

SEED_DEFAULT = "en"
SEED_ENABLED = ("en", "ja", "ko", "zh")
MAX_PRESETS = 12


class LanguageStore:
    """Supported/enabled languages, presets and the removal rules, backed by one JSON file."""

    def __init__(self, settings: Settings, voice_store: VoiceStore) -> None:
        self.settings = settings
        self.voice_store = voice_store
        self.path = settings.data_dir / "languages.json"
        self._lock = threading.RLock()
        self._supported = self._load_supported()
        self._data = self._load_enabled()

    # ------------------------------------------------------------- supported
    def _load_supported(self) -> dict[str, dict[str, str]]:
        path = self.settings.supported_languages_file
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"supported-languages file is unreadable: {path}") from exc
        entries = raw.get("languages") if isinstance(raw, dict) else raw
        out: dict[str, dict[str, str]] = {}
        for item in entries or []:
            code = str(item.get("code", "")).strip().lower()
            if not code:
                continue
            if code in out:
                raise RuntimeError(f"duplicate language code '{code}' in {path}")
            out[code] = {
                "code": code,
                "name": str(item.get("name") or code),
                "native": str(item.get("native") or item.get("name") or code),
            }
        if not out:
            raise RuntimeError(f"no languages in {path}")
        return out

    def supported(self) -> list[dict[str, str]]:
        with self._lock:
            return [dict(entry) for entry in self._supported.values()]

    def is_supported(self, code: str) -> bool:
        with self._lock:
            return code.strip().lower() in self._supported

    # ------------------------------------------------------------------ seed
    def _seed_presets(self) -> dict[str, list[dict[str, str]]]:
        path = self.settings.static_dir / "presets.json"
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("preset seed %s is unreadable; starting with empty presets", path)
            return {code: [] for code in SEED_ENABLED}
        return {
            code: [dict(item) for item in raw.get(code, []) if isinstance(item, dict)]
            for code in SEED_ENABLED
        }

    def _load_enabled(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.warning("%s is corrupt; re-seeding the enabled languages", self.path)
                data = None
            if isinstance(data, dict) and isinstance(data.get("enabled"), list):
                data.setdefault("default", SEED_DEFAULT)
                data.setdefault("presets", {})
                return data
        data = {
            "default": SEED_DEFAULT,
            "enabled": list(SEED_ENABLED),
            "presets": self._seed_presets(),
        }
        self._write(data)
        return data

    def _write(self, data: dict[str, Any] | None = None) -> None:
        data = data if data is not None else self._data
        # Unique tmp name: two LanguageStore instances can share a data dir (app + tests);
        # a fixed name lets one replace() consume another's pending tmp file.
        tmp = self.path.with_name(f"{self.path.name}.{secrets.token_hex(4)}.tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(self.path)

    # ------------------------------------------------------------------ read
    def _enabled(self) -> list[str]:
        return [str(code) for code in self._data["enabled"]]

    def entry(self, code: str, counts: dict[str, int] | None = None) -> dict[str, Any]:
        if counts is None:
            counts = {}
            for voice in self.voice_store.list():
                counts[voice.language] = counts.get(voice.language, 0) + 1
        meta = self._supported.get(code, {"code": code, "name": code, "native": code})
        return {
            "code": code,
            "name": meta["name"],
            "native": meta["native"],
            "default": code == self._data["default"],
            "voices": counts.get(code, 0),
            "presets": len(self._data["presets"].get(code, [])),
            # Drift (SPEC §5a rule 5): still enabled, but the current engine lacks it.
            "supported": code in self._supported,
        }

    def get(self, code: str) -> dict[str, Any]:
        """One enabled language's row; 404 when the code is not enabled."""
        with self._lock:
            self._require_enabled(code)
            return self.entry(code)

    def entries(self) -> list[dict[str, Any]]:
        with self._lock:
            enabled = self._enabled()
            default = str(self._data["default"])
            ordered = ([default] if default in enabled else []) + [
                code for code in enabled if code != default
            ]
            counts: dict[str, int] = {}
            for voice in self.voice_store.list():
                counts[voice.language] = counts.get(voice.language, 0) + 1
            return [self.entry(code, counts) for code in ordered]

    def presets(self, code: str) -> list[dict[str, str]]:
        with self._lock:
            self._require_enabled(code)
            return [dict(item) for item in self._data["presets"].get(code, [])]

    # ----------------------------------------------------------------- write
    def _require_enabled(self, code: str) -> None:
        if code not in self._enabled():
            raise NotFound(f"Unknown language '{code}'.", code="language_not_found")

    def add(self, code: str) -> dict[str, Any]:
        code = code.strip().lower()
        with self._lock:
            if code not in self._supported:
                raise BadRequest(
                    f"Language '{code}' is not supported by the engine.",
                    param="code",
                    code="unsupported_language",
                )
            if code in self._enabled():
                raise APIError(
                    409, f"Language '{code}' is already enabled.", code="language_enabled"
                )
            self._data["enabled"].append(code)
            self._data["presets"].setdefault(code, [])
            self._write()
            return self.entry(code)

    def set_default(self, code: str) -> dict[str, Any]:
        with self._lock:
            self._require_enabled(code)
            self._data["default"] = code
            self._write()
            return self.entry(code)

    def set_presets(self, code: str, items: list[dict[str, str]]) -> list[dict[str, str]]:
        with self._lock:
            self._require_enabled(code)
            if len(items) > MAX_PRESETS:
                raise BadRequest(
                    f"At most {MAX_PRESETS} test phrases per language.",
                    param="presets",
                    code="invalid_presets",
                )
            seen: set[str] = set()
            cleaned: list[dict[str, str]] = []
            for item in items:
                preset = {
                    "id": str(item.get("id", "")).strip(),
                    "label": str(item.get("label", "")).strip(),
                    "text": str(item.get("text", "")).strip(),
                }
                if not all(preset.values()):
                    raise BadRequest(
                        "Each test phrase needs a non-empty id, label and text.",
                        param="presets",
                        code="invalid_presets",
                    )
                if preset["id"] in seen:
                    raise BadRequest(
                        f"Duplicate preset id '{preset['id']}'.",
                        param="presets",
                        code="invalid_presets",
                    )
                seen.add(preset["id"])
                cleaned.append(preset)
            self._data["presets"][code] = cleaned
            self._write()
            return [dict(item) for item in cleaned]

    def remove(self, code: str, voices: str | None = None, to: str | None = None) -> dict[str, Any]:
        """Remove an enabled language; ``voices`` decides what happens to the ones using it."""
        with self._lock:
            self._require_enabled(code)
            if code == self._data["default"] or len(self._enabled()) <= 1:
                raise APIError(
                    409,
                    f"Language '{code}' is the default or the only enabled language.",
                    code="language_locked",
                )
            using = [voice for voice in self.voice_store.list() if voice.language == code]
            if using and voices not in ("relabel", "delete"):
                names = ", ".join(voice.name for voice in using)
                raise APIError(
                    409,
                    f"{len(using)} voice(s) use '{code}': {names}. "
                    "Send voices='relabel' with 'to', or voices='delete'.",
                    code="language_in_use",
                )
            if voices == "relabel":
                target = (to or "").strip().lower()
                if target == code or target not in self._enabled():
                    raise BadRequest(
                        f"Relabel target '{to}' is not another enabled language.",
                        param="to",
                        code="language_not_enabled",
                    )
            else:
                target = None

            # SPEC §5a rule 3: back up voices/ and languages.json before the destructive part.
            self.voice_store.backup(f"language-remove-{code}")
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            self.settings.backups_dir.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                shutil.copy2(self.path, self.settings.backups_dir / f"languages-{stamp}.json")
            for voice in using:
                if voices == "delete":
                    self.voice_store.delete(voice.key)
                elif voices == "relabel":
                    self.voice_store.update(voice.key, language=target)
            self._data["enabled"] = [c for c in self._enabled() if c != code]
            self._data["presets"].pop(code, None)
            self._write()
            return {"removed": code, "voices": voices}

    # ------------------------------------------------------------ enforcement
    def ensure_enabled(self, code: str) -> str:
        """SPEC §5a rule 4: voices may only carry an enabled language. Returns the code."""
        code = (code or "").strip().lower()
        with self._lock:
            if code not in self._enabled():
                raise BadRequest(
                    f"Language '{code}' is not enabled. Enabled: {', '.join(self._enabled())}.",
                    param="language",
                    code="language_not_enabled",
                )
        return code
