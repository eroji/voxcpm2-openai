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

# The sentinel ``run_forever`` records while the engine itself is down. The startup tracker
# must not read it as a warm-up failure: the not-ready path owns the timeout instead.
NOT_READY_ERROR = "engine is not ready"

# run_forever's supervision cadence (module-level so tests can run it fast).
_POLL_S = 2.0
# One failed /health is a blip; warm is only cleared after this many in a row.
_MISSES_BEFORE_UNWARM = 3


class WarmupService:
    def __init__(self, store: VoiceStore, engine: EngineClient, settings: Settings) -> None:
        self.store = store
        self.engine = engine
        self.settings = settings
        self.warm = False
        self.ever_warm = False  # sticky: the startup gate opens once and stays open
        self.last_error: str | None = None

    async def run(self) -> bool:
        if not await self.engine.wait_ready(self.settings.warmup_timeout_s):
            self.warm = False
            self.last_error = NOT_READY_ERROR
            return False
        try:
            voices = self.store.list()
            if voices:
                ref_uri = self.store.data_uri(voices[0].key)
                ref_text: str | None = voices[0].ref_text or None
            else:
                samples, rate = audio.read_audio_file(self.settings.asset_dir / "warmup.wav")
                ref_uri = audio.to_wav_data_uri(samples, rate)
                ref_text = "Warm-up."  # Q-014: the bundled clip's transcript

            # record=False: warm-up must not pollute the RTF / first-audio window the
            # Engine screen shows (ui/studio.html says real requests only).
            await self.engine.synth(
                SynthParams(text="Warm-up.", ref_audio=ref_uri, ref_text=ref_text, fmt="wav"),
                record=False,
            )
            handle = await self.engine.open_stream(
                SynthParams(
                    text="Warm-up.",
                    ref_audio=ref_uri,
                    ref_text=ref_text,
                    fmt="pcm",
                    stream=True,
                ),
                record=False,
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
        self.ever_warm = True
        self.last_error = None
        return True

    async def run_forever(self) -> None:
        """Supervise warm-up for the life of the process: never exits after success.

        A crash-restarted engine flips ``engine.ready`` to False; this loop clears
        ``warm`` and re-warms once the engine is healthy again (SPEC §4/§7). A single
        failed health check does not: warm is only dropped after `_MISSES_BEFORE_UNWARM`
        consecutive misses, so a blip cannot take the Studio down.
        """
        misses = 0
        while True:
            if not self.engine.ready:
                if self.warm and misses < _MISSES_BEFORE_UNWARM - 1:
                    misses += 1
                    await asyncio.sleep(_POLL_S)
                    continue
                misses = 0
                self.warm = False
                self.last_error = NOT_READY_ERROR
                await asyncio.sleep(_POLL_S)
                continue
            misses = 0
            if self.warm:
                await asyncio.sleep(_POLL_S)
                continue
            ok = await self.run()
            await asyncio.sleep(10 if ok else 2)
