"""Engine warm-up and readiness gating (SPEC §4).

The warm-up must finish before ``/ready`` returns 200: one non-streamed synthesis
plus one streamed request, using the first library voice or the bundled clip.
"""

from __future__ import annotations

import asyncio
import logging

from app import audio
from app.config import Settings
from app.engine import EngineClient, SynthParams
from app.store import VoiceStore

logger = logging.getLogger(__name__)


class WarmupService:
    def __init__(self, store: VoiceStore, engine: EngineClient, settings: Settings) -> None:
        self.store = store
        self.engine = engine
        self.settings = settings
        self.warm = False
        self.last_error: str | None = None

    async def run(self) -> bool:
        try:
            voices = self.store.list()
            if voices:
                ref_uri = self.store.data_uri(voices[0].key)
                ref_text: str | None = voices[0].ref_text or None
            else:
                samples, rate = audio.read_audio_file(self.settings.asset_dir / "warmup.wav")
                ref_uri = audio.to_wav_data_uri(samples, rate)
                ref_text = "Warm-up."  # Q-014: the bundled clip's transcript

            await self.engine.synth(
                SynthParams(text="Warm-up.", ref_audio=ref_uri, ref_text=ref_text, fmt="wav")
            )
            handle = await self.engine.open_stream(
                SynthParams(
                    text="Warm-up.",
                    ref_audio=ref_uri,
                    ref_text=ref_text,
                    fmt="pcm",
                    stream=True,
                )
            )
            try:
                async for _ in handle.chunks:
                    pass
            finally:
                await handle.aclose()
        except Exception as exc:  # noqa: BLE001 - warm-up failure only gates /ready
            self.warm = False
            self.last_error = str(exc)
            logger.warning("warm-up failed: %s", exc)
            return False
        self.warm = True
        self.last_error = None
        return True

    async def run_forever(self) -> None:
        while True:
            if not await self.engine.wait_ready(self.settings.warmup_timeout_s):
                self.last_error = "engine is not ready"
                await asyncio.sleep(2)
                continue
            if await self.run():
                break
            await asyncio.sleep(2)
