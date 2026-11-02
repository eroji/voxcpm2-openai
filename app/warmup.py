"""Engine warm-up and readiness gating (SPEC §4).

The warm-up must finish before ``/ready`` returns 200: one non-streamed synthesis
plus one streamed request, using the first library voice or the bundled clip.
"""

from __future__ import annotations

from app.config import Settings
from app.engine import EngineClient
from app.store import VoiceStore


class WarmupService:
    def __init__(self, store: VoiceStore, engine: EngineClient, settings: Settings) -> None:
        self.store = store
        self.engine = engine
        self.settings = settings
        self.warm = False
        self.last_error: str | None = None

    async def run(self) -> bool:
        raise NotImplementedError("M1-T4 implements warm-up")

    async def run_forever(self) -> None:
        raise NotImplementedError("M1-T4 implements warm-up")
