"""Supervise the vLLM-Omni engine process and tee its output to the engine log.

s6 runs this instead of the engine directly so the startup page can show the last engine
lines from ``$DATA_DIR/logs/engine.log`` (ui/STARTING.md, R-046) while container stdout keeps
everything it had before. The log is truncated once at start and then written with
``O_APPEND`` so the startup tracker's ``[app]`` lines and engine lines never clobber each
other; the exit epoch goes to ``$DATA_DIR/logs/engine-last-exit`` for the tracker's
``restart_in_s`` countdown (ui/STARTING.md:34-40).

Env: ``$MODEL_PATH``, ``$DEPLOY_CONFIG``, ``$ENGINE_PORT``, ``$ENGINE_MODEL``.
``ENGINE_COMMAND`` replaces the whole command (test hook).

``$VOXCPM2_CFG_VALUE`` / ``$VOXCPM2_INFERENCE_TIMESTEPS`` (optional) set VoxCPM2's guidance and
diffusion steps for every request. vLLM-Omni only reads them at start, from
``hf_overrides.voxcpm2_runtime_config`` (backported by deploy/patches/voxcpm2_runtime_knobs.py),
so the runner writes a copy of the deploy config with them added; the validated reference
file itself is never edited.
"""

from __future__ import annotations

import ctypes
import math
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


class ConfigError(ValueError):
    """A VOXCPM2_* engine setting is invalid: the engine is not started with it."""


def runtime_knobs() -> dict[str, float | int]:
    """VOXCPM2_CFG_VALUE / VOXCPM2_INFERENCE_TIMESTEPS as voxcpm2_runtime_config keys.

    Unset or empty means the engine default (CFG 2.0, 10 steps).
    """
    knobs: dict[str, float | int] = {}
    cfg = os.environ.get("VOXCPM2_CFG_VALUE", "").strip()
    if cfg:
        try:
            value = float(cfg)
        except ValueError:
            raise ConfigError(f"VOXCPM2_CFG_VALUE={cfg!r} is not a number") from None
        if not math.isfinite(value) or value <= 0:
            raise ConfigError(f"VOXCPM2_CFG_VALUE={cfg!r} must be a positive number")
        knobs["cfg_value"] = value
    steps = os.environ.get("VOXCPM2_INFERENCE_TIMESTEPS", "").strip()
    if steps:
        try:
            count = int(steps)
        except ValueError:
            message = f"VOXCPM2_INFERENCE_TIMESTEPS={steps!r} is not a whole number"
            raise ConfigError(message) from None
        if count < 2:
            raise ConfigError(f"VOXCPM2_INFERENCE_TIMESTEPS={steps!r} must be at least 2")
        knobs["inference_timesteps"] = count
    return knobs


def deploy_config(base: str, knobs: dict[str, float | int]) -> str:
    """The deploy config to start with: ``base`` itself, or a copy with the knobs added to
    every stage's ``engine_extras.hf_overrides.voxcpm2_runtime_config`` (stage 0 when none
    has one), written next to the engine log."""
    if not knobs:
        return base
    import yaml  # ships with vLLM in the image; only needed when a knob is set

    data = yaml.safe_load(Path(base).read_text("utf-8"))
    stages = data.get("stages") or []
    if not stages:
        raise ConfigError(f"{base} has no stages to apply VOXCPM2_* settings to")
    targets = [
        s
        for s in stages
        if "voxcpm2_runtime_config" in ((s.get("engine_extras") or {}).get("hf_overrides") or {})
    ] or stages[:1]
    for stage in targets:
        extras = stage.setdefault("engine_extras", {})
        overrides = extras.setdefault("hf_overrides", {})
        overrides.setdefault("voxcpm2_runtime_config", {}).update(knobs)
    out = Path(os.environ.get("DATA_DIR", "/data")) / "engine-deploy.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(data, sort_keys=False), "utf-8")
    return str(out)


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
        deploy_config(
            os.environ.get("DEPLOY_CONFIG", "/opt/app/deploy/voxcpm2-3090.yaml"), runtime_knobs()
        ),
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
    log = EngineLog(log_path())
    try:
        argv = engine_command()
    except ConfigError as exc:  # say why in the engine log instead of starting with wrong knobs
        _echo(f"[engine] invalid configuration: {exc}\n", log)
        return 2
    knobs = runtime_knobs()
    if knobs:
        _echo(f"[engine] VoxCPM2 runtime settings: {knobs}\n", log)
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
