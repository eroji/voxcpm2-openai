"""One-time import of Qwen3 speaker files (SPEC §5)."""

from __future__ import annotations

from pathlib import Path

from app.store import VoiceStore


def import_qwen3_speakers(
    store: VoiceStore,
    source_dir: Path | str,
    *,
    overwrite: bool = False,
) -> dict[str, list[str]]:
    """Copy ``*.safetensors`` from ``source_dir`` into the voice library.

    Returns ``{"imported": [...], "skipped": [...], "failed": [(name, error)]}``.
    """
    raise NotImplementedError("M2-T1 implements the Qwen3 importer")
