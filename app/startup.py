"""Startup progress tracker for the engine startup page (ui/STARTING.md, CONTRACTS §6b).

Derives the four documented steps from app events, the engine log tail, ``engine.ready``
and ``warmup.warm``. No background task: everything is evaluated lazily in ``snapshot()``,
which the Studio page polls every 2 s.

Two clocks (M2-T16): ``_app_t0`` never moves and drives ``elapsed_s`` ("seconds since the
container started"); ``_attempt_t0`` is re-based whenever a new engine attempt begins (a
manual restart, or a warm engine that went away) and drives the step durations and the
failure timeout, so a restart cannot inherit the previous attempt's age.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from app.config import Settings
    from app.engine import EngineClient
    from app.store import VoiceStore

from app.warmup import NOT_READY_ERROR, WarmupService

logger = logging.getLogger(__name__)

STEP_LABELS = ("Container started", "Loading model weights", "Starting the engine", "Warming up")
WARMUP_DETAIL = "One full and one streamed synthesis, so the first real request is fast"

# ECMA-48: CSI sequences (including private modes such as ESC [ ? 25 l) and OSC strings.
_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")
_EXIT_RE = re.compile(r"\[engine\] exited with code (\d+)")
# Planned stops (s6 restart / SIGINT): not a failure, ui/STARTING.md only means *crashed*.
_PLANNED_EXIT_CODES = frozenset({0, 143, 130})
# nvidia-smi names are far too long for the step detail.
_GPU_PREFIX = "NVIDIA GeForce "

_TAIL_LINES = 200
_TAIL_BYTES = 256 * 1024  # enough for 200 x 200-char lines with headroom
_RETURN_LINES = 16
_LINE_CHARS = 200

# Written by the s6 engine ``finish`` script (M2-T17): crash count and the epoch of the last
# exit. They drive the documented 30/60/120 s automatic-restart countdown.
_FAILURES_FILE = Path("/run/engine-failures")
_LAST_EXIT_FILE = Path("/run/engine-last-exit")
_BACKOFF: tuple[int | None, ...] = (None, 30, 60, 120)


class StartupTracker:
    def __init__(
        self,
        settings: Settings,
        store: VoiceStore | None,
        engine: EngineClient,
        warmup: WarmupService,
    ) -> None:
        self.settings = settings
        self.store = store
        self.engine = engine
        self.warmup = warmup
        # snapshot() runs in a worker thread while the routes call reset()/mark_failed().
        self._lock = threading.Lock()
        self._app_t0 = time.monotonic()
        self._attempt_t0 = self._app_t0
        self._last_done = 0.0  # attempt-relative time of the last step completed
        self._done = [False, False, False, False]
        self._durations: list[int | None] = [None, None, None, None]
        self._voice_count = 0
        self._failed = False
        self._failed_step: int | None = None
        self._error: str | None = None
        self._ever_ready = False
        # Byte offset in engine.log up to which fatal lines have been "seen". Starts at the
        # end of the file: lines from a previous container run are not this attempt's failure.
        self._scan_from, self._scan_ino = self._log_stat()

    # ------------------------------------------------------------------ state

    @property
    def state(self) -> str:
        if self.warmup.warm:
            return "ready"
        if self._failed:
            return "failed"
        return "starting"

    @property
    def ever_ready(self) -> bool:
        """Once the app has been warm it stays usable: a health blip must not re-gate it."""
        return self._ever_ready or bool(self.warmup.warm) or bool(self.warmup.ever_warm)

    def mark_container_started(self, voice_count: int) -> None:
        with self._lock:
            self._voice_count = voice_count
            self._mark_done(0, self._attempt_now())
            self._log_event(f"[app] Voice library loaded: {voice_count} voices")
            host = urlsplit(self.settings.engine_base).netloc or self.settings.engine_url
            self._log_event(f"[app] Waiting for engine on {host}")

    def reset(self) -> None:
        """Restart requested: back to step 2 with a fresh attempt clock (ui/STARTING.md)."""
        with self._lock:
            self._start_attempt()
            self._done = [self._done[0], False, False, False]
            self._durations = [self._durations[0], None, None, None]
            self._scan_from, self._scan_ino = self._log_stat()
            self._log_event("[app] Restart requested")

    def mark_failed(self, message: str, step: int = 3) -> None:
        """A route already knows the attempt failed: show it instead of an endless 'starting'."""
        with self._lock:
            self._failed = True
            self._failed_step = step
            self._error = _plain(message)
            for index in range(step, len(STEP_LABELS)):
                self._done[index] = False
                self._durations[index] = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = self._attempt_now()
            lines, raw = self._read_log()
            engine_ready = bool(self.engine.ready)
            warm = bool(self.warmup.warm)
            if warm:
                self._ever_ready = True

            if self._done[3] and not warm:  # engine lost or warm-up broke after we were ready
                self._done[3] = False
                self._durations[3] = None
                self._start_attempt()
                self._log_event("[app] Engine not ready; re-warming")
            if not self._done[1] and (engine_ready or _contains(lines, "Model loading took")):
                self._mark_done(1, now)
            if engine_ready and not self._done[2]:
                self._mark_done(2, now)
                self._log_event("[app] Engine health OK")
            if warm and not self._done[3]:
                self._mark_done(3, now)
                self._log_event("[app] Warm-up done")
                self._failed = False
                self._failed_step = None
                self._error = None

            if self._failed and engine_ready and self._failed_step != 3:
                # s6 brought the engine back: the not-ready failure no longer applies.
                self._failed = False
                self._failed_step = None
                self._error = None
            if not self._failed and not warm:
                self._detect_failure(now, raw, engine_ready)

            state = self.state
            steps = []
            first_open = next((i for i, done in enumerate(self._done) if not done), 3)
            for i, label in enumerate(STEP_LABELS):
                if self._done[i]:
                    status, duration = "done", self._durations[i]
                elif state == "failed" and i == self._failed_step:
                    status, duration = "failed", None
                elif i == first_open:
                    status, duration = "active", None
                else:
                    status, duration = "waiting", None
                steps.append(
                    {
                        "label": label,
                        "detail": self._detail(i),
                        "status": status,
                        "duration_s": duration,
                    }
                )
            return {
                "state": state,
                "elapsed_s": int(round(time.monotonic() - self._app_t0)),
                "steps": steps,
                "error": self._error if state == "failed" else None,
                "restart_in_s": self._restart_in(state),
                "log": lines[-_RETURN_LINES:],
            }

    # --------------------------------------------------------------- internals

    def _detect_failure(self, now: float, raw: bytes, engine_ready: bool) -> None:
        step: int | None = None
        message = self._fatal_message(_scan_lines(raw, self._scan_from))
        if message is None and not engine_ready and now > self.settings.startup_fail_after_s:
            message = (
                f"The engine did not become healthy within "
                f"{int(self.settings.startup_fail_after_s)} seconds. See the engine log."
            )
        # A non-sentinel warm-up error means the last attempt ran against a healthy engine
        # and failed: the re-warm loop owns it, so the step must say so. The "engine is not
        # ready" sentinel belongs to the timeout above.
        error = self.warmup.last_error
        if message is None and engine_ready and error not in (None, NOT_READY_ERROR):
            message, step = _plain(error), 3
        if message is None:
            return
        self._failed = True
        self._error = message
        if step is None:
            step = next((i for i, done in enumerate(self._done) if not done), 3)
        self._failed_step = step
        self.warmup.warm = False
        self._scan_from = len(raw)

    def _start_attempt(self) -> None:
        self._attempt_t0 = time.monotonic()
        self._last_done = 0.0
        self._failed = False
        self._failed_step = None
        self._error = None

    def _mark_done(self, index: int, now: float) -> None:
        self._done[index] = True
        self._durations[index] = int(round(now - self._last_done))
        self._last_done = now

    def _attempt_now(self) -> float:
        return time.monotonic() - self._attempt_t0

    def _restart_in(self, state: str) -> int | None:
        """Seconds until s6's backed-off restart, or None when there is none pending."""
        if state == "ready":
            return None
        try:
            failures = int(_FAILURES_FILE.read_text(encoding="utf-8").strip())
            last_exit = float(_LAST_EXIT_FILE.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        if not 1 <= failures < len(_BACKOFF):
            return None
        backoff = _BACKOFF[failures]
        assert backoff is not None  # guaranteed by the range check above
        return max(0, math.ceil(backoff - (time.time() - last_exit)))

    def _detail(self, index: int) -> str:
        if index == 0:
            return f"App online, voice library loaded ({self._voice_count} voices)"
        if index == 1:
            return f"{self.settings.engine_model} · 4.7 GB from {self.settings.model_path}"
        if index == 2:
            stats = self.engine.stats()  # never raises; nvidia-smi is cached internally
            runtime = stats.runtime or "vLLM-Omni"
            kv = stats.kv_cache or self.settings.kv_cache_label
            gpu = (stats.gpu or "GPU").removeprefix(_GPU_PREFIX)
            return f"{runtime} · {kv} KV cache · {gpu}"
        return WARMUP_DETAIL

    def _fatal_message(self, lines: list[str]) -> str | None:
        for line in lines:
            if "CUDA out of memory" in line:
                return (
                    "CUDA out of memory while allocating the KV cache. "
                    "Another process may be using GPU memory."
                )
            if "No module named 'voxcpm'" in line:
                return (
                    "The voxcpm package is missing from the image. "
                    "Rebuild with voxcpm installed via uv."
                )
            if "Engine core initialization failed" in line:
                return "The engine failed to start. See the engine log."
            match = _EXIT_RE.search(line)
            if match and int(match.group(1)) not in _PLANNED_EXIT_CODES:
                return f"The engine process exited with code {match.group(1)}. See the engine log."
        return None

    def _log_path(self) -> Path:
        return self.settings.data_dir / "logs" / "engine.log"

    def _log_stat(self) -> tuple[int, int]:
        """(size, inode) of the log; (0, 0) when it cannot be read yet."""
        try:
            stat = self._log_path().stat()
        except OSError:
            return 0, 0
        return stat.st_size, stat.st_ino

    def _read_log(self) -> tuple[list[str], bytes]:
        """(display tail, whole readable bytes). Missing/unreadable log is simply empty."""
        try:
            stat = self._log_path().stat()
            raw = self._log_path().read_bytes()
        except OSError:
            return [], b""
        if stat.st_ino != self._scan_ino or len(raw) < self._scan_from:
            self._scan_from = 0  # rotated or truncated: everything in it is new again
            self._scan_ino = stat.st_ino
        text = raw[-_TAIL_BYTES:].decode("utf-8", "replace")
        lines = [_ANSI_RE.sub("", line).strip()[:_LINE_CHARS] for line in text.splitlines()]
        return lines[-_TAIL_LINES:], raw

    def _log_event(self, line: str) -> None:
        """Append an app event to the engine log; the file must never break the app."""
        try:
            path = self._log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            logger.debug("startup log write failed", exc_info=True)


def _contains(lines: list[str], needle: str) -> bool:
    return any(needle in line for line in lines)


def _plain(message: str) -> str:
    """One plain-language sentence, never a traceback (ui/STARTING.md)."""
    return message.strip().splitlines()[0][:_LINE_CHARS] if message.strip() else message


def _scan_lines(raw: bytes, start: int) -> list[str]:
    """Log lines appended since the watermark: what a *new* failure can hide in."""
    return _ANSI_RE.sub("", raw[start:].decode("utf-8", "replace")).splitlines()
