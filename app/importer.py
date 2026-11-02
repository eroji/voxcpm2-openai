"""One-time import of Qwen3 speaker files (SPEC §5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from safetensors import safe_open

from app.store import VoiceStore

_LANG_BY_SUFFIX = {"-en": "en", "-ja": "ja", "-kr": "ko", "-zh": "zh"}


def _language_for(name: str) -> str:
    lowered = name.lower()
    for suffix, language in _LANG_BY_SUFFIX.items():
        if lowered.endswith(suffix):
            return language
    return ""


def import_qwen3_speakers(
    store: VoiceStore,
    source_dir: Path | str,
    *,
    overwrite: bool = False,
) -> dict[str, list[Any]]:
    """Copy ``*.safetensors`` from ``source_dir`` into the voice library.

    Returns ``{"imported": [...], "skipped": [...], "failed": [(name, error)]}``.
    One corrupt or invalid file never aborts the rest of the import.
    """
    result: dict[str, list[Any]] = {"imported": [], "skipped": [], "failed": []}
    source = Path(source_dir)
    if not source.is_dir():
        return result
    for path in sorted(source.glob("*.safetensors")):
        try:
            with safe_open(str(path), framework="numpy") as handle:
                meta = {str(k): str(v) for k, v in (handle.metadata() or {}).items()}
                samples = np.asarray(handle.get_tensor("audio"), dtype=np.float32)
        except Exception as exc:  # noqa: BLE001 - corrupt file: record and continue
            result["failed"].append((path.stem, f"{type(exc).__name__}: {exc}"))
            continue
        key = meta.get("voice_name_lower") or path.stem
        name = meta.get("name") or key
        if not overwrite and store.exists(key):
            result["skipped"].append(name)
            continue
        consent = meta.get("consent") or "uploaded-via-studio"
        try:
            store.create(
                name,
                samples,
                int(meta.get("sample_rate") or 24000),
                ref_text=meta.get("ref_text", ""),
                language=_language_for(key),
                consent=consent,
                source="designed" if consent == "own-generated-voice" else "uploaded",
                metadata=meta,
                overwrite=overwrite,
                key=key,
            )
        except Exception as exc:  # noqa: BLE001 - invalid name or IO error: record and continue
            result["failed"].append((name, f"{type(exc).__name__}: {exc}"))
            continue
        result["imported"].append(name)
    return result
