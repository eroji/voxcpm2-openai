"""app.engine_runner: tee the engine output to $DATA_DIR/logs/engine.log (M2-T15a, M2-T17)."""

import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from app.engine_runner import ConfigError, EngineLog, engine_command

ROOT = Path(__file__).resolve().parents[1]


def _env(tmp_path: Path, command: str) -> dict[str, str]:
    env = dict(os.environ, DATA_DIR=str(tmp_path), ENGINE_COMMAND=command, PYTHONPATH=str(ROOT))
    env.pop("ENGINE_MODEL", None)
    return env


def _run(tmp_path: Path, code: str) -> subprocess.CompletedProcess[str]:
    command = shlex.join([sys.executable, "-c", code])
    return subprocess.run(
        [sys.executable, "-m", "app.engine_runner"],
        cwd=ROOT,
        env=_env(tmp_path, command),
        capture_output=True,
        text=True,
        timeout=60,
    )


def _log(tmp_path: Path) -> Path:
    return tmp_path / "logs" / "engine.log"


def _rotated(tmp_path: Path) -> Path:
    return tmp_path / "logs" / "engine.log.1"


def test_default_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_PATH", "/models/openbmb/VoxCPM2")
    monkeypatch.setenv("DEPLOY_CONFIG", "/opt/app/deploy/voxcpm2-3090.yaml")
    monkeypatch.setenv("ENGINE_PORT", "8102")
    monkeypatch.delenv("ENGINE_MODEL", raising=False)
    monkeypatch.delenv("ENGINE_COMMAND", raising=False)
    monkeypatch.delenv("VOXCPM2_CFG_VALUE", raising=False)
    monkeypatch.delenv("VOXCPM2_INFERENCE_TIMESTEPS", raising=False)
    assert engine_command() == [
        "vllm",
        "serve",
        "/models/openbmb/VoxCPM2",
        "--omni",
        "--deploy-config",
        "/opt/app/deploy/voxcpm2-3090.yaml",
        "--served-model-name",
        "openbmb/VoxCPM2",
        "--host",
        "127.0.0.1",
        "--port",
        "8102",
    ]
    monkeypatch.setenv("ENGINE_MODEL", "voxcpm2")
    assert engine_command()[7] == "voxcpm2"


def test_lines_reach_stdout_and_log(tmp_path: Path) -> None:
    res = _run(tmp_path, "print('hello engine')\nprint('second line')")
    assert res.returncode == 0, res.stderr
    text = _log(tmp_path).read_text()
    for line in ("hello engine", "second line", "[engine] exited with code 0"):
        assert line in res.stdout, line
        assert line in text, line


def test_child_exit_code_is_propagated(tmp_path: Path) -> None:
    res = _run(tmp_path, "print('boom')\nraise SystemExit(3)")
    assert res.returncode == 3
    assert "[engine] exited with code 3" in _log(tmp_path).read_text()


def test_sigterm_stops_the_child(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    code = (
        "import os, pathlib, time\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    command = shlex.join([sys.executable, "-c", code])
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.engine_runner"],
        cwd=ROOT,
        env=_env(tmp_path, command),
        stdout=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 30
    while not pid_file.exists():
        if time.monotonic() > deadline:
            proc.kill()
            pytest.fail("engine child never started")
        time.sleep(0.05)
    child_pid = int(pid_file.read_text())

    proc.send_signal(signal.SIGTERM)
    out = proc.communicate(timeout=30)[0]

    assert proc.returncode in (143, 0), proc.returncode  # 128+SIGTERM on a planned stop
    assert "[engine] exited with code" in out
    for _ in range(100):  # the child must be gone, not orphaned
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"engine child {child_pid} survived the runner")


def test_log_rotates_to_1_when_too_big(tmp_path: Path) -> None:
    _log(tmp_path).parent.mkdir(parents=True)
    _log(tmp_path).write_text("x" * (2 * 1024 * 1024 + 10))
    res = _run(tmp_path, "print('fresh')")
    assert res.returncode == 0, res.stderr
    assert len(_rotated(tmp_path).read_text()) == 2 * 1024 * 1024 + 10
    text = _log(tmp_path).read_text()
    assert "fresh" in text and "xxxx" not in text and len(text) < 200


def test_mid_run_rotation(tmp_path: Path) -> None:
    log = EngineLog(_log(tmp_path), max_bytes=1024)
    for _ in range(300):
        log.write("y" * 40 + "\n")
    assert len(_rotated(tmp_path).read_text()) > 1024
    assert len(_log(tmp_path).read_text()) < 1024


def test_unwritable_log_keeps_stdout_working(tmp_path: Path) -> None:
    blocked = tmp_path / "data"
    blocked.write_text("not a directory")
    command = shlex.join([sys.executable, "-c", "print('to stdout only')"])
    res = subprocess.run(
        [sys.executable, "-m", "app.engine_runner"],
        cwd=ROOT,
        env=_env(blocked, command),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert res.returncode == 0, res.stderr
    assert "to stdout only" in res.stdout


# ------------------------------------------------------------------ M2-T17: log safety


def _wait_for_file(path: Path, needle: str, timeout: float = 30.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            text = ""
        if needle in text:
            return text
        time.sleep(0.05)
    pytest.fail(f"{needle!r} never appeared in {path}")


def test_append_mode_survives_concurrent_app_writes(tmp_path: Path) -> None:
    # The startup tracker appends [app] lines to the same file while the engine runs;
    # opening with "w" used to clobber them (M2-T17 review finding).
    child = (
        "import time\n"
        "print('engine one', flush=True)\n"
        "time.sleep(2)\n"
        "print('engine two', flush=True)\n"
    )
    command = shlex.join([sys.executable, "-c", child])
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.engine_runner"],
        cwd=ROOT,
        env=_env(tmp_path, command),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_for_file(_log(tmp_path), "engine one")
        with _log(tmp_path).open("a", encoding="utf-8") as fh:  # the tracker's writer
            fh.write("[app] tracker line\n")
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    text = _log(tmp_path).read_text(errors="replace")
    for line in ("engine one", "[app] tracker line", "engine two"):
        assert line in text, line


def test_invalid_utf8_from_engine_does_not_kill_the_runner(tmp_path: Path) -> None:
    res = _run(
        tmp_path,
        "import sys\nsys.stdout.buffer.write(b'caf\\xe9 \\xff ok\\n')\nsys.stdout.buffer.flush()",
    )
    assert res.returncode == 0, res.stderr
    assert "ok" in _log(tmp_path).read_text(errors="replace")


def test_missing_engine_command_logs_and_exits_127(tmp_path: Path) -> None:
    res = subprocess.run(
        [sys.executable, "-m", "app.engine_runner"],
        cwd=ROOT,
        env=_env(tmp_path, "/nonexistent/vllm-binary"),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert res.returncode == 127, res.stderr
    assert "[engine] failed to start" in res.stdout
    assert "[engine] failed to start" in _log(tmp_path).read_text()


def test_exit_epoch_written_for_the_countdown(tmp_path: Path) -> None:
    _run(tmp_path, "print('x')")
    marker = tmp_path / "logs" / "engine-last-exit"
    assert abs(int(marker.read_text()) - time.time()) < 120


def test_rotation_counts_file_size_not_own_writes(tmp_path: Path) -> None:
    log = EngineLog(_log(tmp_path), max_bytes=200)
    log.write("e" * 100 + "\n")
    with _log(tmp_path).open("a", encoding="utf-8") as fh:  # the tracker pushes it over
        fh.write("a" * 200 + "\n")
    log.write("done\n")  # own bytes alone (105) are under the cap: only st_size rotates
    assert _rotated(tmp_path).exists()
    assert "done" in _rotated(tmp_path).read_text()
    assert _log(tmp_path).exists()  # fresh file after the rotation


def test_long_lines_are_capped(tmp_path: Path) -> None:
    log = EngineLog(_log(tmp_path))
    log.write("z" * 100_000 + "\n")
    assert len(_log(tmp_path).read_text()) <= 8 * 1024


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="PR_SET_PDEATHSIG is Linux-only")
def test_sigkill_of_the_runner_does_not_orphan_the_child(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    code = (
        "import os, pathlib, time\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    command = shlex.join([sys.executable, "-c", code])
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.engine_runner"],
        cwd=ROOT,
        env=_env(tmp_path, command),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 30
    while not pid_file.exists():
        if time.monotonic() > deadline:
            proc.kill()
            proc.wait()
            pytest.fail("engine child never started")
        time.sleep(0.05)
    child_pid = int(pid_file.read_text())

    proc.kill()  # SIGKILL: no handler runs, PDEATHSIG must stop the child
    proc.wait(timeout=30)
    deadline = time.monotonic() + 10
    while True:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        if time.monotonic() > deadline:
            os.kill(child_pid, signal.SIGKILL)
            pytest.fail(f"engine child {child_pid} outlived a SIGKILLed runner")
        time.sleep(0.05)


# --- VOXCPM2_CFG_VALUE / VOXCPM2_INFERENCE_TIMESTEPS (vllm-omni PR #7010 backport) ----------

REFERENCE = ROOT / "deploy" / "voxcpm2-3090.yaml"


def _knob_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cfg: str, steps: str) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DEPLOY_CONFIG", str(REFERENCE))
    monkeypatch.delenv("ENGINE_COMMAND", raising=False)
    monkeypatch.setenv("VOXCPM2_CFG_VALUE", cfg)
    monkeypatch.setenv("VOXCPM2_INFERENCE_TIMESTEPS", steps)


def test_runtime_knobs_go_into_a_copy_of_the_deploy_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reference = REFERENCE.read_text("utf-8")
    _knob_env(monkeypatch, tmp_path, "2.8", "12")
    argv = engine_command()
    derived = Path(argv[argv.index("--deploy-config") + 1])
    assert derived == tmp_path / "engine-deploy.yaml"
    runtime = yaml.safe_load(derived.read_text("utf-8"))["stages"][0]["engine_extras"][
        "hf_overrides"
    ]["voxcpm2_runtime_config"]
    assert runtime["cfg_value"] == 2.8 and runtime["inference_timesteps"] == 12
    assert runtime["vae_decode_every"] == 3  # the validated settings are kept
    assert REFERENCE.read_text("utf-8") == reference  # the reference file is never edited


def test_no_runtime_knobs_uses_the_reference_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _knob_env(monkeypatch, tmp_path, "", "")
    argv = engine_command()
    assert argv[argv.index("--deploy-config") + 1] == str(REFERENCE)
    assert not (tmp_path / "engine-deploy.yaml").exists()


@pytest.mark.parametrize(
    ("cfg", "steps"), [("abc", ""), ("0", ""), ("nan", ""), ("", "1"), ("", "ten")]
)
def test_invalid_runtime_knobs_are_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cfg: str, steps: str
) -> None:
    _knob_env(monkeypatch, tmp_path, cfg, steps)
    with pytest.raises(ConfigError):
        engine_command()


def test_invalid_runtime_knob_is_logged_and_engine_not_started(tmp_path: Path) -> None:
    env = dict(_env(tmp_path, ""), VOXCPM2_CFG_VALUE="-1", DEPLOY_CONFIG=str(REFERENCE))
    env.pop("ENGINE_COMMAND")
    res = subprocess.run(
        [sys.executable, "-m", "app.engine_runner"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert res.returncode == 2
    assert "invalid configuration: VOXCPM2_CFG_VALUE='-1'" in _log(tmp_path).read_text()
