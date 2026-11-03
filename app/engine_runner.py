"""Supervise the vLLM-Omni engine process and tee its output to the engine log.

s6 runs this instead of the engine directly so the startup page can show the last engine
lines from ``$DATA_DIR/logs/engine.log`` (ui/STARTING.md, R-046) while container stdout keeps
everything it had before. The log is truncated once at start and then written with
``O_APPEND`` so the startup tracker's ``[app]`` lines and engine lines never clobber each
other; the exit epoch goes to ``$DATA_DIR/logs/engine-last-exit`` for the tracker's
``restart_in_s`` countdown (ui/STARTING.md:34-40).

Env: ``$MODEL_PATH``, ``$DEPLOY_CONFIG``, ``$ENGINE_PORT``, ``$ENGINE_MODEL``.
``ENGINE_COMMAND`` replaces the whole command (test hook).
"""

from __future__ import annotations

import ctypes
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TextIO

LOG_MAX_BYTES = 2 * 1024 * 1024
LINE_MAX_CHARS = 8 * 1024  # one progress-bar line must not eat the whole log


def engine_command() -> list[str]:
    """The engine command: vllm serve $MODEL_PATH --omni --deploy-config $DEPLOY_CONFIG
    --served-model-name $ENGINE_MODEL --host 127.0.0.1 --port $ENGINE_PORT."""
    override = os.environ.get("ENGINE_COMMAND", "").strip()
    if override:
        return shlex.split(override)
    return [
        "vllm",
        "serve",
        os.environ.get("MODEL_PATH", "/models/openbmb/VoxCPM2"),
        "--omni",
        "--deploy-config",
        os.environ.get("DEPLOY_CONFIG", "/opt/app/deploy/voxcpm2-3090.yaml"),
        "--served-model-name",
        os.environ.get("ENGINE_MODEL", "openbmb/VoxCPM2"),
        "--host",
        "127.0.0.1",
        "--port",
        os.environ.get("ENGINE_PORT", "8102"),
    ]


def log_path() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data")) / "logs" / "engine.log"


def _set_parent_death_sig() -> None:
    """PR_SET_PDEATHSIG in the child: a SIGKILLed runner must not orphan vllm. Best effort."""
    try:
        ctypes.CDLL(None).prctl(1, int(signal.SIGTERM), 0, 0, 0)  # 1 = PR_SET_PDEATHSIG
    except Exception:
        pass


class EngineLog:
    """Line sink that never raises: stdout must keep working even without the file.

    Append mode only after the one-time startup truncation: the startup tracker writes
    ``[app]`` lines to the same file, so rotation watches ``st_size``, not our own bytes.
    """

    def __init__(self, path: Path, max_bytes: int = LOG_MAX_BYTES) -> None:
        self.path = path
        self.max_bytes = max_bytes
        self._fh: TextIO | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and self._size() > max_bytes:
                self._rotate()
            path.write_text("")  # truncate once here, never again: every write appends
            self._fh = path.open("a", encoding="utf-8", errors="replace")
        except OSError:
            self._fh = None

    def _size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def _rotate(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        os.replace(self.path, self.path.with_name(f"{self.path.name}.1"))

    def write(self, text: str) -> None:
        if self._fh is None:
            return
        if len(text) > LINE_MAX_CHARS:
            text = text[: LINE_MAX_CHARS - 1] + "\n"
        try:
            self._fh.write(text)
            self._fh.flush()
            if self._size() > self.max_bytes:
                self._rotate()
                self._fh = self.path.open("a", encoding="utf-8", errors="replace")
        except OSError:
            self._fh = None


def _echo(text: str, log: EngineLog) -> None:
    """Write to container stdout and the log file; a dead log reader must not kill the engine."""
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except (OSError, UnicodeError):
        pass
    log.write(text)


def _write_last_exit() -> None:
    try:
        path = log_path().with_name("engine-last-exit")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(int(time.time())))
    except OSError:
        pass


def main() -> int:
    argv = engine_command()
    log = EngineLog(log_path())
    _echo(f"[engine] running: {shlex.join(argv)}\n", log)

    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",  # a stray byte from the engine must not kill the supervisor
            bufsize=1,
            start_new_session=True,
            preexec_fn=_set_parent_death_sig if sys.platform == "linux" else None,
        )
    except OSError as exc:  # missing vllm: without this the s6 loop logs nothing at all
        _echo(f"[engine] failed to start: {exc}\n", log)
        return 127

    def forward(signum: int, _frame: object) -> None:
        try:  # start_new_session=True makes the engine its own process-group leader
            os.killpg(proc.pid, signum)
        except OSError:
            pass

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, forward)

    assert proc.stdout is not None  # stdout=PIPE
    for line in proc.stdout:
        _echo(line, log)

    code = proc.wait()
    if code < 0:  # killed by a signal: report it the way a shell does, 128 + signum
        code = 128 - code
    _echo(f"[engine] exited with code {code}\n", log)
    _write_last_exit()
    return code


if __name__ == "__main__":
    sys.exit(main())
