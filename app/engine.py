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

import asyncio
import contextlib
import json
import logging
import shutil
import subprocess
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

import httpx

from app.audio import duration_s, mime_for, read_audio
from app.errors import EngineError, EngineNotReady, QueueFull

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger("app.engine")

_SPEECH_PATH = "/v1/audio/speech"
_SAMPLE_RATE = 48000  # engine-native rate (SPEC §2)


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
        kv_cache: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.concurrency = concurrency
        self.max_queue = max_queue
        self.metrics_window = metrics_window
        self.timeout = timeout
        self.s6_service = s6_service
        self.service_dir = service_dir
        self.kv_cache = kv_cache

        self._client: httpx.AsyncClient | None = None
        self._ready = False
        self._poll_task: asyncio.Task[None] | None = None
        self._sem = asyncio.Semaphore(concurrency)
        self._waiting = 0
        self._rtf: deque[float] = deque(maxlen=metrics_window)
        self._first: deque[float] = deque(maxlen=metrics_window)
        self._supports_cfg_steps = True
        self._gpu_cache: tuple[float, tuple[str | None, int | None, int | None]] = (
            0.0,
            (None, None, None),
        )

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
            kv_cache=settings.kv_cache_label,
        )

    @property
    def ready(self) -> bool:
        return self._ready

    async def start(self) -> None:
        self._http()
        await self.health()
        if self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll(), name="engine-health-poll")

    async def aclose(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def health(self) -> bool:
        try:
            resp = await self._http().get("/health", timeout=5.0)
            self._ready = resp.is_success
        except httpx.HTTPError:
            self._ready = False
        return self._ready

    async def wait_ready(self, max_wait: float) -> bool:
        deadline = time.monotonic() + max_wait
        while True:
            if await self.health():
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.5)

    async def synth(self, params: SynthParams) -> SynthResult:
        async with self._slot():
            await self._ensure_ready()
            started = time.perf_counter()
            resp = await self._post(params, stream=False)
            wall = time.perf_counter() - started
        samples, rate = read_audio(resp.content)
        dur = duration_s(samples, rate)
        rtf = wall / dur if dur > 0 else None
        # First-audio latency is meaningful for streaming only (SPEC §4).
        self._record(rtf, None)
        return SynthResult(
            audio=resp.content,
            content_type=resp.headers.get("content-type", mime_for("wav")),
            duration_s=dur,
            rtf=rtf,
            first_audio_ms=None,
        )

    def stream(self, params: SynthParams) -> AsyncIterator[bytes]:
        return self._stream(params)

    async def restart(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            "s6-svc",
            "-r",
            f"{self.service_dir}/{self.s6_service}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except TimeoutError:
            proc.kill()
            raise EngineError("engine restart timed out after 30 s") from None
        if proc.returncode != 0:
            detail = stderr.decode("utf-8", "replace").strip()[:300] if stderr else ""
            raise EngineError(f"s6-svc -r failed: {detail or proc.returncode}")

    def stats(self) -> EngineStats:
        gpu, used, total = self._gpu_info()
        return EngineStats(
            ready=self._ready,
            model=self.model,
            runtime=_runtime_label(),
            gpu=gpu,
            vram_used_mb=used,
            vram_total_mb=total,
            kv_cache=self.kv_cache,
            max_concurrency=self.concurrency,
            rtf=sum(self._rtf) / len(self._rtf) if self._rtf else None,
            first_audio_ms=sum(self._first) / len(self._first) if self._first else None,
            queue_depth=self._waiting,
        )

    # ---------------------------------------------------------------- internals

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout)
        return self._client

    async def _poll(self) -> None:
        while True:
            await self.health()
            await asyncio.sleep(2.0)

    @asynccontextmanager
    async def _slot(self) -> AsyncIterator[None]:
        if self._sem.locked() and self._waiting >= self.max_queue:
            raise QueueFull()
        self._waiting += 1
        try:
            await self._sem.acquire()
        finally:
            self._waiting -= 1
        try:
            yield
        finally:
            self._sem.release()

    async def _ensure_ready(self) -> None:
        if not self._ready:
            await self.health()
        if not self._ready:
            raise EngineNotReady()

    def _payload(self, params: SynthParams, *, cfg_steps: bool) -> dict[str, object]:
        body: dict[str, object] = {
            "model": self.model,
            "input": params.text,
            "response_format": params.fmt,
            "stream": params.stream,
        }
        if params.ref_audio is not None:
            body["ref_audio"] = params.ref_audio
        if params.ref_text is not None:
            body["ref_text"] = params.ref_text
        for key, value in (
            ("instructions", params.instructions),
            ("speed", params.speed),
            ("seed", params.seed),
        ):
            if value is not None:
                body[key] = value
        if cfg_steps:
            if params.cfg is not None:
                body["cfg"] = params.cfg
            if params.steps is not None:
                body["steps"] = params.steps
        return body

    async def _post(self, params: SynthParams, *, stream: bool) -> httpx.Response:
        """POST to the engine; on 4xx caused by cfg/steps retry once without them (Q-004)."""
        client = self._http()
        body = self._payload(params, cfg_steps=self._supports_cfg_steps)
        while True:
            if stream:
                request = client.build_request("POST", _SPEECH_PATH, json=body)
                resp = await client.send(request, stream=True)
            else:
                resp = await client.post(_SPEECH_PATH, json=body)
            if resp.status_code < 400:
                return resp
            message = await _error_message(resp)
            rejected = (
                resp.status_code < 500
                and self._supports_cfg_steps
                and ("cfg" in body or "steps" in body)
            )
            await resp.aclose()
            if rejected:
                self._supports_cfg_steps = False
                logger.warning(
                    "engine rejected cfg/steps (HTTP %s): %s — retrying without them",
                    resp.status_code,
                    message,
                )
                body = self._payload(params, cfg_steps=False)
                continue
            raise EngineError(message)

    async def _stream(self, params: SynthParams) -> AsyncIterator[bytes]:
        """Engine streams s16le mono 48 kHz (SPEC §6); pass it through unchanged."""
        async with self._slot():
            await self._ensure_ready()
            started = time.perf_counter()
            resp = await self._post(params, stream=True)
            first_ms: float | None = None
            total = 0
            try:
                buf = bytearray()
                async for raw in resp.aiter_raw():
                    buf += raw
                    frames = len(buf) - len(buf) % 2
                    if frames == 0:
                        continue
                    out = bytes(buf[:frames])
                    del buf[:frames]
                    if first_ms is None:
                        first_ms = (time.perf_counter() - started) * 1000.0
                    total += len(out)
                    yield out
            finally:
                await resp.aclose()
            wall = time.perf_counter() - started
        dur = total / 2 / _SAMPLE_RATE
        self._record(wall / dur if dur > 0 else None, first_ms)

    def _record(self, rtf: float | None, first_ms: float | None) -> None:
        if rtf is not None:
            self._rtf.append(rtf)
        if first_ms is not None:
            self._first.append(first_ms)

    def _gpu_info(self) -> tuple[str | None, int | None, int | None]:
        now = time.monotonic()
        if now - self._gpu_cache[0] < 2.0:
            return self._gpu_cache[1]
        info: tuple[str | None, int | None, int | None] = (None, None, None)
        try:
            if shutil.which("nvidia-smi"):
                out = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=name,memory.used,memory.total",
                        "--format=csv,noheader,nounits",
                        "-i",
                        "0",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    check=False,
                ).stdout
                name, used, total = (part.strip() for part in out.split(","))
                info = (name, int(used), int(total))
        except Exception:  # noqa: BLE001 - stats() must never raise
            info = (None, None, None)
        self._gpu_cache = (now, info)
        return info


def _runtime_label() -> str:
    parts: list[str] = []
    for name, label in (("vllm-omni", "vLLM-Omni"), ("vllm", "vLLM")):
        try:
            parts.append(f"{label} {version(name)}")
        except PackageNotFoundError:
            continue
    return " · ".join(parts) if parts else "vLLM-Omni"


async def _error_message(resp: httpx.Response) -> str:
    fallback = f"engine returned HTTP {resp.status_code}"
    try:
        raw = resp.content if resp.is_stream_consumed else await resp.aread()
        data = json.loads(raw.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - upstream body shape is not our contract
        return fallback
    node = data.get("error") if isinstance(data, dict) else None
    if not isinstance(node, dict):
        node = data if isinstance(data, dict) else {}
    message = node.get("message") or node.get("detail")
    return str(message)[:500] if message else fallback
