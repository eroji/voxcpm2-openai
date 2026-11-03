"""Startup tracker + startup-page routing (M2-T15b/T16, ui/STARTING.md, CONTRACTS §6b)."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest
from app import startup as startup_module
from app.config import Settings
from app.main import create_app
from app.startup import StartupTracker
from fastapi.testclient import TestClient

CUDA_MSG = (
    "CUDA out of memory while allocating the KV cache. Another process may be using GPU memory."
)
MODULE_MSG = "The voxcpm package is missing from the image. Rebuild with voxcpm installed via uv."


class StubEngine:
    """Just the surface the tracker uses: ``ready`` and ``stats()``."""

    def __init__(self, gpu: str | None = None) -> None:
        self._ready = False
        self._gpu = gpu

    @property
    def ready(self) -> bool:
        return self._ready

    def stats(self):
        return SimpleNamespace(runtime="vLLM-Omni 0.30.0", kv_cache="2 GiB", gpu=self._gpu)


class StubWarmup:
    def __init__(self) -> None:
        self.warm = False
        self.ever_warm = False
        self.last_error: str | None = None


def make_tracker(settings: Settings, gpu: str | None = None):
    engine, warmup = StubEngine(gpu), StubWarmup()
    tracker = StartupTracker(settings, None, engine, warmup)
    tracker.mark_container_started(2)
    return tracker, engine, warmup


def log_file(settings: Settings):
    return settings.data_dir / "logs" / "engine.log"


def write_log(settings: Settings, *lines: str) -> None:
    path = log_file(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_log(settings: Settings, *lines: str) -> None:
    path = log_file(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def statuses(snapshot: dict) -> list[str]:
    return [step["status"] for step in snapshot["steps"]]


# --------------------------------------------------------------------- tracker


def test_progression(settings: Settings) -> None:
    settings.startup_fail_after_s = 3600
    tracker, engine, warmup = make_tracker(settings)

    snap = tracker.snapshot()
    assert snap["state"] == "starting"
    assert statuses(snap) == ["done", "active", "waiting", "waiting"]
    steps = snap["steps"]
    assert steps[0]["detail"] == "App online, voice library loaded (2 voices)"
    assert settings.model_path in steps[1]["detail"]
    assert steps[2]["detail"] == "vLLM-Omni 0.30.0 · 2 GiB KV cache · GPU"  # gpu fallback
    assert steps[3]["detail"] == (
        "One full and one streamed synthesis, so the first real request is fast"
    )

    append_log(settings, "INFO Model loading took 4.31 GiB and 19.8 s")
    assert statuses(tracker.snapshot()) == ["done", "done", "active", "waiting"]

    engine._ready = True
    assert statuses(tracker.snapshot()) == ["done", "done", "done", "active"]

    warmup.warm = True
    snap = tracker.snapshot()
    assert snap["state"] == "ready"
    assert statuses(snap) == ["done"] * 4
    assert all(step["duration_s"] is not None for step in snap["steps"])


def test_gpu_detail_drops_the_vendor_prefix(settings: Settings) -> None:
    settings.startup_fail_after_s = 3600
    tracker, _, _ = make_tracker(settings, gpu="NVIDIA GeForce RTX 3090")
    detail = tracker.snapshot()["steps"][2]["detail"]
    assert detail == "vLLM-Omni 0.30.0 · 2 GiB KV cache · RTX 3090"


def test_fatal_cuda_marks_current_step_failed(settings: Settings) -> None:
    settings.startup_fail_after_s = 3600
    tracker, _, warmup = make_tracker(settings)
    append_log(settings, "INFO Model loading took 4.31 GiB and 19.8 s", "ERROR CUDA out of memory.")
    snap = tracker.snapshot()
    assert snap["state"] == "failed"
    assert snap["error"] == CUDA_MSG
    assert statuses(snap) == ["done", "done", "failed", "waiting"]
    assert warmup.warm is False


def test_fatal_cuda_before_model_load(settings: Settings) -> None:
    settings.startup_fail_after_s = 3600
    tracker, _, _ = make_tracker(settings)
    append_log(settings, "ERROR CUDA out of memory. Tried to allocate 2.00 GiB.")
    snap = tracker.snapshot()
    assert snap["state"] == "failed"
    assert snap["error"] == CUDA_MSG
    assert statuses(snap) == ["done", "failed", "waiting", "waiting"]


def test_fatal_missing_module(settings: Settings) -> None:
    settings.startup_fail_after_s = 3600
    tracker, _, _ = make_tracker(settings)
    append_log(settings, "ImportError: No module named 'voxcpm'")
    snap = tracker.snapshot()
    assert snap["state"] == "failed" and snap["error"] == MODULE_MSG


def test_fatal_engine_core_init(settings: Settings) -> None:
    settings.startup_fail_after_s = 3600
    tracker, _, _ = make_tracker(settings)
    append_log(settings, "ERROR Engine core initialization failed. See logs above.")
    snap = tracker.snapshot()
    assert snap["state"] == "failed"
    assert snap["error"] == "The engine failed to start. See the engine log."


def test_fatal_exit_code(settings: Settings) -> None:
    settings.startup_fail_after_s = 3600
    tracker, _, _ = make_tracker(settings)
    append_log(settings, "[engine] exited with code 2")
    snap = tracker.snapshot()
    assert snap["state"] == "failed"
    assert snap["error"] == "The engine process exited with code 2. See the engine log."


def test_planned_exit_code_is_not_fatal(settings: Settings) -> None:
    settings.startup_fail_after_s = 3600
    tracker, _, _ = make_tracker(settings)
    append_log(settings, "[engine] exited with code 143")
    assert tracker.snapshot()["state"] == "starting"


def test_stale_log_lines_are_not_this_attempts_failure(settings: Settings) -> None:
    """Lines from a previous container run must not fail the app before it even tries."""
    settings.startup_fail_after_s = 3600
    write_log(settings, "ERROR CUDA out of memory.")
    tracker, _, _ = make_tracker(settings)
    assert tracker.snapshot()["state"] == "starting"

    append_log(settings, "ERROR CUDA out of memory.")  # a crash in *this* attempt does
    assert tracker.snapshot()["state"] == "failed"


def test_rotated_log_is_scanned_from_the_start(settings: Settings, tmp_path) -> None:
    settings.startup_fail_after_s = 3600
    tracker, _, _ = make_tracker(settings)
    rotated = tmp_path / "rotated.log"
    rotated.write_text("x" * 4096 + "\nERROR CUDA out of memory.\n", encoding="utf-8")
    log_file(settings).parent.mkdir(parents=True, exist_ok=True)
    rotated.replace(log_file(settings))  # new inode, larger than the watermark
    assert tracker.snapshot()["state"] == "failed"


def test_timeout_failure(settings: Settings) -> None:
    settings.startup_fail_after_s = 0
    tracker, _, _ = make_tracker(settings)
    snap = tracker.snapshot()
    assert snap["state"] == "failed"
    assert snap["error"] == (
        "The engine did not become healthy within 0 seconds. See the engine log."
    )


def test_timeout_uses_the_attempt_clock(settings: Settings, monkeypatch) -> None:
    """An app that has been up for an hour must not fail a restart it just started."""
    settings.startup_fail_after_s = 1
    tracker, _, _ = make_tracker(settings)
    monkeypatch.setattr(tracker, "_app_t0", time.monotonic() - 3600)
    tracker.reset()
    snap = tracker.snapshot()
    assert snap["state"] == "starting"
    assert snap["elapsed_s"] >= 3600

    monkeypatch.setattr(tracker, "_attempt_t0", time.monotonic() - 2)
    assert tracker.snapshot()["state"] == "failed"


def test_reset_clears_failure_and_skips_old_lines(settings: Settings) -> None:
    settings.startup_fail_after_s = 3600
    tracker, _, _ = make_tracker(settings)
    append_log(settings, "ERROR CUDA out of memory.")
    assert tracker.snapshot()["state"] == "failed"

    tracker.reset()
    snap = tracker.snapshot()
    assert snap["state"] == "starting" and snap["error"] is None
    assert statuses(snap) == ["done", "active", "waiting", "waiting"]  # back to step 2
    assert "[app] Restart requested" in log_file(settings).read_text(encoding="utf-8")

    append_log(settings, "ERROR CUDA out of memory.")  # a *new* crash still trips
    assert tracker.snapshot()["state"] == "failed"


def test_reset_keeps_elapsed_monotonic(settings: Settings, monkeypatch) -> None:
    settings.startup_fail_after_s = 3600
    tracker, _, _ = make_tracker(settings)
    monkeypatch.setattr(tracker, "_app_t0", time.monotonic() - 100)
    before = tracker.snapshot()
    assert before["elapsed_s"] >= 100
    tracker.reset()
    after = tracker.snapshot()
    assert after["elapsed_s"] >= before["elapsed_s"]
    assert after["steps"][0]["duration_s"] == before["steps"][0]["duration_s"]


def test_post_warm_engine_loss_reopens_step_four(settings: Settings) -> None:
    settings.startup_fail_after_s = 3600
    tracker, engine, warmup = make_tracker(settings)
    engine._ready = True
    warmup.warm = True
    assert statuses(tracker.snapshot()) == ["done"] * 4

    warmup.warm = False  # the engine went away after we were ready
    snap = tracker.snapshot()
    assert snap["state"] == "starting"
    assert statuses(snap) == ["done", "done", "done", "active"]
    assert "[app] Engine not ready; re-warming" in log_file(settings).read_text(encoding="utf-8")

    warmup.last_error = "engine stream failed: connection reset"  # re-warm cannot recover it
    snap = tracker.snapshot()
    assert snap["state"] == "failed"
    assert statuses(snap) == ["done", "done", "done", "failed"]
    assert snap["error"] == "engine stream failed: connection reset"


def test_warmup_error_while_engine_ready_marks_failed(settings: Settings) -> None:
    """A warm-up that keeps failing against a healthy engine must reach `failed`."""
    settings.startup_fail_after_s = 3600
    tracker, engine, warmup = make_tracker(settings)
    engine._ready = True
    assert statuses(tracker.snapshot()) == ["done", "done", "done", "active"]

    warmup.last_error = "The engine returned no audio"
    snap = tracker.snapshot()
    assert snap["state"] == "failed"
    assert statuses(snap) == ["done", "done", "done", "failed"]
    assert snap["error"] == "The engine returned no audio"


def test_not_ready_error_is_not_a_warmup_failure(settings: Settings) -> None:
    """The not-ready sentinel belongs to the timeout path, never to step 4."""
    settings.startup_fail_after_s = 3600
    tracker, engine, warmup = make_tracker(settings)
    warmup.last_error = "engine is not ready"
    engine._ready = True  # the engine answers again before the first warm-up attempt
    snap = tracker.snapshot()
    assert snap["state"] == "starting"
    assert statuses(snap) == ["done", "done", "done", "active"]


def test_mark_failed_from_a_route(settings: Settings) -> None:
    settings.startup_fail_after_s = 3600
    tracker, _, _ = make_tracker(settings)
    append_log(settings, "INFO Model loading took 4.31 GiB and 19.8 s")
    tracker.snapshot()

    tracker.mark_failed("s6-svc -r failed: unable to contact s6", step=2)
    snap = tracker.snapshot()
    assert snap["state"] == "failed"
    assert statuses(snap) == ["done", "done", "failed", "waiting"]
    assert snap["error"] == "s6-svc -r failed: unable to contact s6"


def test_restart_in_s_counts_down_the_s6_backoff(settings: Settings, tmp_path, monkeypatch) -> None:
    settings.startup_fail_after_s = 3600
    failures, last_exit = tmp_path / "engine-failures", tmp_path / "engine-last-exit"
    monkeypatch.setattr(startup_module, "_FAILURES_FILE", failures)
    monkeypatch.setattr(startup_module, "_LAST_EXIT_FILE", last_exit)
    tracker, _, _ = make_tracker(settings)
    assert tracker.snapshot()["restart_in_s"] is None  # no s6 state: nothing to count

    failures.write_text("1\n", encoding="utf-8")
    last_exit.write_text(str(time.time() - 10), encoding="utf-8")
    assert tracker.snapshot()["restart_in_s"] == 20

    failures.write_text("2\n", encoding="utf-8")
    last_exit.write_text(str(time.time() - 10), encoding="utf-8")
    assert tracker.snapshot()["restart_in_s"] == 50

    failures.write_text("3\n", encoding="utf-8")
    last_exit.write_text(str(time.time() - 130), encoding="utf-8")
    assert tracker.snapshot()["restart_in_s"] == 0  # the sleep is over, s6 is on it

    failures.write_text("4\n", encoding="utf-8")  # backed off for good: manual restart only
    assert tracker.snapshot()["restart_in_s"] is None


def test_log_tail_16_lines_ansi_stripped(settings: Settings) -> None:
    settings.startup_fail_after_s = 3600
    long_line = "x" * 500
    tracker, _, _ = make_tracker(settings)  # its [app] lines are truncated away below
    write_log(settings, *[f"\x1b[31mINFO line {i}\x1b[0m" for i in range(300)], long_line)
    snap = tracker.snapshot()
    assert len(snap["log"]) == 16
    assert all("\x1b" not in line and len(line) <= 200 for line in snap["log"])
    assert snap["log"][-1] == "x" * 200  # oldest first, trimmed to 200 chars
    assert snap["log"][-2] == "INFO line 299"


def test_private_and_osc_sequences_are_stripped(settings: Settings) -> None:
    settings.startup_fail_after_s = 3600
    tracker, _, _ = make_tracker(settings)
    write_log(settings, "\x1b[?25l\x1b]0;engine title\x07INFO plain \x1b[1;32mok\x1b[0m")
    assert tracker.snapshot()["log"][-1] == "INFO plain ok"


def test_app_events_are_logged(settings: Settings) -> None:
    settings.startup_fail_after_s = 3600
    tracker, engine, warmup = make_tracker(settings)
    engine._ready = True
    warmup.warm = True
    tracker.snapshot()
    text = log_file(settings).read_text(encoding="utf-8")
    assert "[app] Voice library loaded: 2 voices" in text
    assert "[app] Waiting for engine on" in text
    assert "[app] Engine health OK" in text
    assert "[app] Warm-up done" in text


# ---------------------------------------------------------------------- HTTP


@pytest.fixture
def starting_client(settings: Settings):
    settings.startup_page = True
    settings.engine_url = "http://127.0.0.1:9"  # dead: nothing answers, warm-up never runs
    settings.startup_fail_after_s = 3600
    with TestClient(create_app(settings)) as client:
        yield client


def test_startup_page_and_api_gate(starting_client: TestClient) -> None:
    response = starting_client.get("/")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"  # the page reloads itself
    assert "Starting the voice engine" in response.text

    response = starting_client.get("/api/voices")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert response.json()["error"]["message"]

    response = starting_client.get("/api/engine/startup")
    assert response.status_code == 200
    snap = response.json()
    assert snap["state"] in ("starting", "failed")
    assert len(snap["steps"]) == 4
    assert snap["restart_in_s"] is None
    assert set(snap) == {"state", "elapsed_s", "steps", "error", "restart_in_s", "log"}

    response = starting_client.post("/api/engine/restart")
    assert response.status_code != 503  # 502: no s6-svc here, but never the gate
    assert starting_client.get("/api/engine/startup").json()["state"] == "failed"

    assert starting_client.get("/health").status_code == 200

    response = starting_client.post("/v1/audio/speech", json={"input": "hi", "voice": "nobody"})
    assert response.json()["error"].get("code") != "startup_in_progress"  # /v1 is not gated


def test_gate_blocks_everything_but_the_two_startup_paths(starting_client: TestClient) -> None:
    for method, path in (
        ("GET", "/api/engine/startup"),
        ("GET", "/api/engine/startup/"),
        ("POST", "/api/engine/restart"),
        ("POST", "/api/engine/restart/"),
    ):
        assert starting_client.request(method, path).status_code != 503, path
    for path in ("/api/voices", "/api/voices/", "/api/takes", "/api/engine"):
        assert starting_client.get(path).status_code == 503, path


def test_gate_stays_open_once_the_app_has_been_ready(settings: Settings, fake_engine: Any) -> None:
    """A single health blip must not lock the Studio behind a 503 again."""
    settings.startup_page = True
    with TestClient(create_app(settings)) as client:
        client.app.state.warmup.warm = True
        client.app.state.warmup.ever_warm = True
        assert client.get("/api/voices").status_code == 200

        client.app.state.warmup.warm = False
        assert client.get("/api/voices").status_code == 200
        assert "screen-speak" in client.get("/").text


def test_failed_state_message_in_the_gate(settings: Settings) -> None:
    settings.startup_page = True
    settings.engine_url = "http://127.0.0.1:9"
    settings.startup_fail_after_s = 0
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/engine/startup").json()["state"] == "failed"
        response = client.get("/api/voices")
        assert response.status_code == 503
        assert "failed" in response.json()["error"]["message"].lower()
        assert "Starting the voice engine" in client.get("/").text


def test_ready_serves_studio(settings: Settings, fake_engine: Any) -> None:
    settings.startup_page = True  # fake_engine fixture keeps the engine healthy
    with TestClient(create_app(settings)) as client:
        client.app.state.warmup.warm = True
        assert "screen-speak" in client.get("/").text
        assert client.get("/api/voices").status_code == 200
