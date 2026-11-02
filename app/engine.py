"""Client for the local vLLM-Omni VoxCPM2 engine (SPEC §4, §6).

Contract frozen for wave 2+:

- ``synth`` returns the engine's WAV bytes and records rolling metrics.
- ``stream`` yields normalized s16le mono PCM at 48 kHz.
- Concurrency is capped at ``settings.engine_concurrency``; at most
  ``settings.max_queue`` requests wait, then ``QueueFull`` (HTTP 429).
- ``ready`` mirrors the last ``/health`` result. Warm-up is owned by
  ``app.warmup``, not this client.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.errors import EngineError, EngineNotReady, QueueFull  # noqa: F401 - re-exported

if TYPE_CHECKING:
    from app.config import Settings


@dataclass(slots=True)
class SynthParams:
    text: str
    ref_audio: str | None = None
    ref_text: str | None = None
    instructions: str | None = None
    speed: float | None = None
    seed: int | None = None
    cfg: float | None = None
    steps: int | None = None
    fmt: str = "wav"  # wav (non-stream) | pcm (stream)
    stream: bool = False


@dataclass(slots=True)
class SynthResult:
    audio: bytes
    content_type: str
    duration_s: float
    rtf: float | None = None
    first_audio_ms: float | None = None


@dataclass(slots=True)
class EngineStats:
    ready: bool
    model: str
    runtime: str
    gpu: str | None
    vram_used_mb: int | None
    vram_total_mb: int | None
    kv_cache: str | None
    max_concurrency: int
    rtf: float | None
    first_audio_ms: float | None
    queue_depth: int


class EngineClient:
    def __init__(
        self,
        base_url: str,
        *,
        model: str = "openbmb/VoxCPM2",
        concurrency: int = 2,
        max_queue: int = 16,
        metrics_window: int = 20,
        timeout: float = 300.0,
        s6_service: str = "engine",
        service_dir: str = "/run/service",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.concurrency = concurrency
        self.max_queue = max_queue
        self.metrics_window = metrics_window
        self.timeout = timeout
        self.s6_service = s6_service
        self.service_dir = service_dir

    @classmethod
    def from_settings(cls, settings: Settings) -> EngineClient:
        return cls(
            settings.engine_base,
            model=settings.engine_model,
            concurrency=settings.engine_concurrency,
            max_queue=settings.max_queue,
            metrics_window=settings.metrics_window,
            timeout=settings.request_timeout_s,
            s6_service=settings.s6_service,
        )

    @property
    def ready(self) -> bool:
        raise NotImplementedError("M1-T1 implements the engine client")

    async def start(self) -> None:
        raise NotImplementedError("M1-T1 implements the engine client")

    async def aclose(self) -> None:
        raise NotImplementedError("M1-T1 implements the engine client")

    async def health(self) -> bool:
        raise NotImplementedError("M1-T1 implements the engine client")

    async def wait_ready(self, max_wait: float) -> bool:
        raise NotImplementedError("M1-T1 implements the engine client")

    async def synth(self, params: SynthParams) -> SynthResult:
        raise NotImplementedError("M1-T1 implements the engine client")

    def stream(self, params: SynthParams) -> AsyncIterator[bytes]:
        raise NotImplementedError("M1-T1 implements the engine client")

    async def restart(self) -> None:
        raise NotImplementedError("M1-T1 implements the engine client")

    def stats(self) -> EngineStats:
        raise NotImplementedError("M1-T1 implements the engine client")
