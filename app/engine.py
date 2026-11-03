"""Client for the local vLLM-Omni VoxCPM2 engine (SPEC §4, §6).

Contract frozen for wave 2+:

- ``synth`` returns the engine's WAV bytes and records rolling metrics.
- ``open_stream`` admits the request (queue/readiness) **before** returning a
  ``StreamHandle`` so 429/503 reach the router before response headers; the
  handle yields normalized s16le mono PCM at 48 kHz and releases the slot
  exactly once on exhaustion, cancel or ``aclose()``.
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
import threading
import time
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from app.audio import duration_s, mime_for, read_audio
from app.errors import EngineError, EngineNotReady, QueueFull

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger("app.engine")

_SPEECH_PATH = "/v1/audio/speech"
ENGINE_SAMPLE_RATE = 48_000  # engine-native rate (SPEC §2); single source of truth


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


@dataclass(slots=True)
class StreamHandle:
    """A live engine stream.

    ``chunks`` yields s16le mono 48 kHz and releases the engine slot in its
    ``finally``; ``aclose`` is idempotent and safe after exhaustion.
    """

    chunks: AsyncGenerator[bytes, None]
    aclose: Callable[[], Awaitable[None]]


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
        self._supports_instructions = True
        # stats() runs in a worker thread (asyncio.to_thread) while the event
        # loop appends metrics; the lock keeps deque snapshots consistent.
        self._stats_lock = threading.Lock()
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

    async def synth(self, params: SynthParams, *, record: bool = True) -> SynthResult:
        async with self._slot():
            await self._ensure_ready()
            started = time.perf_counter()
            resp = await self._post(params, stream=False)
            wall = time.perf_counter() - started
        try:
            samples, rate = read_audio(resp.content)
        except Exception as exc:  # noqa: BLE001 - undecodable upstream body
            raise EngineError(f"engine returned undecodable audio: {exc}") from exc
        dur = duration_s(samples, rate)
        rtf = wall / dur if dur > 0 else None
        # First-audio latency is meaningful for streaming only (SPEC §4).
        self._record(rtf if record else None, None)
        return SynthResult(
            audio=resp.content,
            content_type=resp.headers.get("content-type", mime_for("wav")),
            duration_s=dur,
            rtf=rtf,
            first_audio_ms=None,
        )

    async def open_stream(self, params: SynthParams, *, record: bool = True) -> StreamHandle:
        """Admit the request (queue/readiness) *before* returning, then open the stream.

        Admission raises ``QueueFull``/``EngineNotReady`` here — never after the
        router has already sent ``200`` (SPEC §4/§6). The slot is held by the
        returned handle and released exactly once when its chunks finish.
        ``record=False`` keeps the request out of the RTF / first-audio window (warm-up).
        """
        await self._acquire()
        started = time.perf_counter()  # includes the engine's pre-header latency
        try:
            await self._ensure_ready()
            resp = await self._post(params, stream=True)
        except BaseException:
            # _post closes any error response it received before raising.
            self._release()
            raise

        released = False

        async def aclose() -> None:
            nonlocal released
            if released:
                return
            released = True
            try:
                with contextlib.suppress(Exception):
                    await resp.aclose()
            finally:
                # CancelledError is a BaseException: the slot must come back
                # even when resp.aclose() is interrupted.
                self._release()

        async def chunks() -> AsyncGenerator[bytes, None]:
            """Engine streams s16le mono 48 kHz (SPEC §6); pass it through unchanged."""
            first_ms: float | None = None
            total = 0
            try:
                buf = bytearray()
                try:
                    async for raw in resp.aiter_bytes():
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
                except httpx.HTTPError as exc:
                    raise EngineError(f"engine stream failed: {exc}") from exc
                wall = time.perf_counter() - started
                dur = total / 2 / ENGINE_SAMPLE_RATE
                if record:
                    self._record(wall / dur if dur > 0 else None, first_ms)
            finally:
                await aclose()

        return StreamHandle(chunks=chunks(), aclose=aclose)

    async def restart(self) -> None:
        """Restart the s6 service and *wait* for it to be healthy again.

        ``s6-svc -r`` returns immediately, so callers (the restart route) must
        never warm the old process: mark down, restart, then poll /health for
        up to 120 s (SPEC §4/§7). The ``finish`` script's backoff state is cleared
        first and the service is brought back up, or a manual restart would sit in
        the 120 s hold-down with the service marked down (M2-T17).
        """
        self._ready = False
        self._clear_failure_markers()
        await self._s6("-u")  # already-up is not an error here; -r below is authoritative
        code, stderr = await self._s6("-r")
        if code != 0:
            detail = stderr.decode("utf-8", "replace").strip()[:300] if stderr else ""
            raise EngineError(f"s6-svc -r failed: {detail or code}")
        await asyncio.sleep(0.5)  # give s6 a moment to bring the service back up
        if not await self.wait_ready(120):
            raise EngineError("engine did not become healthy after restart")

    async def _s6(self, flag: str) -> tuple[int, bytes]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "s6-svc",
                flag,
                f"{self.service_dir}/{self.s6_service}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise EngineError(f"cannot run s6-svc: {exc}") from exc
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except TimeoutError:
            proc.kill()
            await proc.wait()  # reap, no zombie
            raise EngineError("engine restart timed out after 30 s") from None
        return int(proc.returncode or 0), stderr

    def _clear_failure_markers(self) -> None:
        """Drop the s6 backoff state: a manual restart starts the counter over."""
        run_dir = Path(self.service_dir).parent
        for path in (
            Path(self.service_dir) / self.s6_service / "down",
            run_dir / "engine-failures",
            run_dir / "engine-last-exit",
        ):
            with contextlib.suppress(OSError):
                path.unlink()

    def stats(self) -> EngineStats:
        gpu, used, total = self._gpu_info()
        with self._stats_lock:  # snapshot while the event loop may append
            rtf = sum(self._rtf) / len(self._rtf) if self._rtf else None
            first = sum(self._first) / len(self._first) if self._first else None
        return EngineStats(
            ready=self._ready,
            model=self.model,
            runtime=_runtime_label(),
            gpu=gpu,
            vram_used_mb=used,
            vram_total_mb=total,
            kv_cache=self.kv_cache,
            max_concurrency=self.concurrency,
            rtf=rtf,
            first_audio_ms=first,
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

    async def _acquire(self) -> None:
        if self._sem.locked() and self._waiting >= self.max_queue:
            raise QueueFull()
        self._waiting += 1
        try:
            await self._sem.acquire()
        finally:
            self._waiting -= 1

    def _release(self) -> None:
        self._sem.release()

    @asynccontextmanager
    async def _slot(self) -> AsyncIterator[None]:
        await self._acquire()
        try:
            yield
        finally:
            self._release()

    async def _ensure_ready(self) -> None:
        if not self._ready:
            await self.health()
        if not self._ready:
            raise EngineNotReady()

    def _payload(
        self, params: SynthParams, *, cfg_steps: bool, instructions: bool = True
    ) -> dict[str, object]:
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
        if instructions and params.instructions is not None:
            body["instructions"] = params.instructions
        for key, value in (("speed", params.speed), ("seed", params.seed)):
            if value is not None:
                body[key] = value
        if cfg_steps:
            if params.cfg is not None:
                body["cfg"] = params.cfg
            if params.steps is not None:
                body["steps"] = params.steps
        return body

    async def _post(self, params: SynthParams, *, stream: bool) -> httpx.Response:
        """POST to the engine.

        Upstream 429/503 map to ``QueueFull``/``EngineNotReady`` (with their
        ``Retry-After`` semantics); a 4xx naming ``cfg``/``steps`` (Q-004) or
        ``instructions`` (S1 fallback) retries once without that field.
        """
        client = self._http()
        body = self._payload(
            params, cfg_steps=self._supports_cfg_steps, instructions=self._supports_instructions
        )
        while True:
            try:
                if stream:
                    request = client.build_request("POST", _SPEECH_PATH, json=body)
                    resp = await client.send(request, stream=True)
                else:
                    resp = await client.post(_SPEECH_PATH, json=body)
            except httpx.HTTPError as exc:
                raise EngineError(f"engine request failed: {exc}") from exc
            if resp.status_code < 400:
                return resp
            status = resp.status_code
            message = await _error_message(resp)
            await resp.aclose()
            if status == 429:
                raise QueueFull(_retry_after_seconds(resp))
            if status == 503:
                raise EngineNotReady(message)
            lowered = message.lower()
            if status in (400, 415, 422):  # only a schema rejection, never on an unrelated 4xx
                if (
                    self._supports_cfg_steps
                    and ("cfg" in body or "steps" in body)
                    and ("cfg" in lowered or "steps" in lowered)
                ):
                    self._supports_cfg_steps = False
                    logger.warning(
                        "engine rejected cfg/steps (HTTP %s): %s — retrying without them",
                        status,
                        message,
                    )
                    body = self._payload(
                        params,
                        cfg_steps=False,
                        instructions=self._supports_instructions,
                    )
                    continue
                if (
                    self._supports_instructions
                    and "instructions" in body
                    and "instructions" in lowered
                ):
                    self._supports_instructions = False
                    logger.warning(
                        "engine rejected instructions (HTTP %s): %s — retrying without it",
                        status,
                        message,
                    )
                    body = self._payload(
                        params,
                        cfg_steps=self._supports_cfg_steps,
                        instructions=False,
                    )
                    continue
            raise EngineError(message)

    def _record(self, rtf: float | None, first_ms: float | None) -> None:
        with self._stats_lock:
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


def _retry_after_seconds(resp: httpx.Response, default: int = 1) -> int:
    try:
        return max(1, int(resp.headers.get("retry-after", "")))
    except ValueError:
        return default


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
