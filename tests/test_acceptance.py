"""M5-T2 — SPEC §13 acceptance suite.

Every §13 bullet is either asserted here against the fake engine + temp data dir,
or skipped with a reason naming the external blocker (Q-001 / M0 / CI / human
ears). Mapping:

- WAV 48 kHz + warm RTF ≤ 0.2      -> test_wav_48k_and_rtf (fake-engine plumbing smoke);
                                      the 3090 RTF number -> test_hardware_rtf_and_vram (skipped)
- Streamed PCM first chunk ≤ 300ms -> test_stream_first_chunk_and_total
- Eight imported voices            -> test_import_eight_voices_synth (ear check skipped)
- Warm-up before /ready            -> test_warm_start
- Clone flow                       -> test_clone_flow
- Design flow                      -> test_design_flow
- LiteLLM/Open WebUI/Hermes/Wyoming-> skipped (external clients)
- VRAM ≤ 10 GB / card sharing      -> test_hardware_rtf_and_vram (skipped: host nvidia-smi)
- Burst of 20: queue or clean 429  -> test_burst_queues_or_429s
- UI matches ui/studio.html        -> static parts asserted, browser parity skipped
"""

from __future__ import annotations

import io
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np
import pytest
from app.audio import pcm_s16le_to_float, wav_bytes
from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from safetensors.numpy import save_file

BODY = {"model": "tts-1", "voice": "ashley-en", "input": "hello"}

# SPEC §5: the eight Qwen3 speakers; language is derived from the key suffix (-kr -> ko).
QWEN3_VOICES = {
    "ashley-en": "en",
    "ashley-ja": "ja",
    "ashley-kr": "ko",
    "ashley-zh": "zh",
    "alison-en": "en",
    "bennett-en": "en",
    "jack-en": "en",
    "mia-en": "en",
}


def _until(predicate: Callable[[], bool], timeout: float = 10.0) -> None:
    """Poll until true or fail at the deadline — no fixed sleeps."""
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition never became true"
        time.sleep(0.05)


def _client(settings: Settings, **fields: Any) -> TestClient:
    """Extra app on the same data dir/engine with the named settings overridden."""
    return TestClient(create_app(Settings(**{**settings.model_dump(), **fields})))


def _tone(seconds: float, rate: int = 24000) -> np.ndarray:
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


# --- §13: WAV, RTF --------------------------------------------------------------


def test_wav_48k_and_rtf(client: TestClient, library: Any) -> None:
    r = client.post("/v1/audio/speech", json={**BODY, "response_format": "wav"})
    assert r.status_code == 200
    assert r.content[:4] == b"RIFF"
    assert int.from_bytes(r.content[24:28], "little") == 48000
    # Smoke check only: RTF against the fake engine proves the X-RTF header plumbing,
    # NOT the SPEC §13 hardware criterion — that is test_hardware_rtf_and_vram (skipped)
    # and scripts/smoke.sh on the 3090 host.
    assert float(r.headers["X-RTF"]) <= 0.2


# --- §13: streaming first chunk --------------------------------------------------


def test_stream_first_chunk_and_total(client: TestClient, library: Any) -> None:
    started = time.perf_counter()
    with client.stream(
        "POST", "/v1/audio/speech", json={**BODY, "stream": True, "response_format": "pcm"}
    ) as r:
        assert r.status_code == 200
        first_ms = None
        parts = []
        for chunk in r.iter_raw():
            if chunk:
                if first_ms is None:
                    first_ms = (time.perf_counter() - started) * 1000.0
                parts.append(chunk)
    assert first_ms is not None and first_ms <= 300.0, f"first chunk took {first_ms:.0f} ms"
    body = b"".join(parts)
    assert len(body) % 2 == 0  # s16le frames
    assert len(pcm_s16le_to_float(body)) / 48000 == pytest.approx(0.6, abs=0.05)


# --- §13: eight imported voices ---------------------------------------------------


def test_import_eight_voices_synth(client: TestClient, settings: Settings) -> None:
    src = settings.import_dir
    src.mkdir(parents=True, exist_ok=True)
    for name in QWEN3_VOICES:
        save_file(
            {"audio": _tone(1.0)},
            str(src / f"{name}.safetensors"),
            metadata={
                "name": name,
                "voice_name_lower": name,
                "ref_text": "Thanks for waiting. Everything checks out.",
                "sample_rate": "24000",
                "created_at": "1791831655",
                "consent": "uploaded-via-studio",
                "embedding_source": "audio",
                "mime_type": "audio/wav",
                "original_filename": f"{name}.wav",
                "file_size": "48044",
            },
        )
    r = client.post("/api/voices/import")
    assert r.status_code == 200
    assert sorted(r.json()["imported"]) == sorted(QWEN3_VOICES)
    listed = {v["key"]: v["language"] for v in client.get("/api/voices").json()["voices"]}
    assert listed == QWEN3_VOICES
    for name in QWEN3_VOICES:
        assert client.post("/v1/audio/speech", json={**BODY, "voice": name}).status_code == 200


# --- §13: warm start --------------------------------------------------------------


def test_warm_start(client: TestClient, fake_engine: Any) -> None:
    assert client.get("/health").status_code == 200
    _until(lambda: client.get("/ready").status_code == 200)
    # /ready turned 200 without this test synthesizing: warm-up ran at startup.
    assert fake_engine.state["calls"] >= 2  # one WAV + one stream (SPEC §4)


# --- §13: clone flow --------------------------------------------------------------


def test_clone_flow(client: TestClient) -> None:
    r = client.post(
        "/api/voices/clone",
        files={
            "file": ("ref.wav", io.BytesIO(wav_bytes(_tone(6.0), 24000)), "audio/wav"),
            "name": (None, "acceptance-clone"),
            "language": (None, "en"),
            "ref_text": (None, "hello there"),
            "consent": (None, "true"),
        },
    )
    assert r.status_code == 200, r.text
    key = r.json()["key"]
    assert key in {v["key"] for v in client.get("/api/voices").json()["voices"]}
    assert key in {v["name"] for v in client.get("/v1/audio/voices").json()["voices"]}
    assert client.post("/v1/audio/speech", json={**BODY, "voice": key}).status_code == 200


# --- §13: design flow -------------------------------------------------------------


def test_design_flow(client: TestClient) -> None:
    r = client.post(
        "/api/design/candidates",
        json={
            "description": "A warm female voice, a calm narrator in her thirties.",
            "sample_text": "Good morning, here is a sample of the voice.",
            "language": "en",
            "n": 3,
        },
    )
    assert r.status_code == 200
    candidates = r.json()["candidates"]
    assert len(candidates) == 3
    chosen = candidates[1]
    r = client.post(
        "/api/design/save",
        json={"candidate_id": chosen["id"], "name": "acceptance-designed", "language": "en"},
    )
    assert r.status_code == 200, r.text
    assert "acceptance-designed" in {v["key"] for v in client.get("/api/voices").json()["voices"]}
    assert (
        client.post("/v1/audio/speech", json={**BODY, "voice": "acceptance-designed"}).status_code
        == 200
    )


# --- §13: burst / queueing ---------------------------------------------------------


def test_burst_queues_or_429s(settings: Settings, library: Any, fake_engine: Any) -> None:
    with _client(settings, engine_concurrency=2, max_queue=16) as c:
        _until(lambda: c.get("/ready").status_code == 200)
        # Hold each "slow" synth 1.0 s (instead of the 0.25 s default) so all 20
        # barrier-released threads reach admission while the first 18 are still
        # in flight — the 18-admitted/2-rejected split is deterministic even on
        # a loaded runner, not a race against a 250 ms window.
        fake_engine.state["delay"] = 1.0
        barrier = threading.Barrier(20, timeout=30)

        def fire(_: int) -> tuple[int, str | None]:
            barrier.wait()
            r = c.post("/v1/audio/speech", json={**BODY, "input": "slow and steady wins"})
            return r.status_code, r.headers.get("Retry-After")

        try:
            with ThreadPoolExecutor(max_workers=20) as pool:
                results = list(pool.map(fire, range(20)))
        finally:
            fake_engine.state["delay"] = 0.25
    codes = [code for code, _ in results]
    assert all(code in (200, 429) for code in codes), codes  # never 5xx
    assert codes.count(200) >= 18, codes  # 2 active + 16 queued (Q-016)
    assert 429 in codes, codes
    for code, retry_after in results:
        if code == 429:
            assert retry_after, "429 without Retry-After"


# --- §13: errors -------------------------------------------------------------------


def test_errors(client: TestClient, library: Any) -> None:
    r = client.post("/v1/audio/speech", json={**BODY, "voice": "nobody"})
    assert r.status_code == 400
    assert "ashley-en" in r.json()["error"]["message"]  # lists the available names
    assert client.post("/v1/audio/speech", json={**BODY, "input": "x" * 4001}).status_code == 400
    assert client.get("/api/takes/doesnotexist/audio").status_code == 404


# --- §13 / §10: auth ---------------------------------------------------------------


def test_api_keys_guard_v1_not_system(settings: Settings, library: Any) -> None:
    with _client(settings, api_keys="secret") as c:
        assert c.post("/v1/audio/speech", json=BODY).status_code == 401
        bad = c.post("/v1/audio/speech", json=BODY, headers={"Authorization": "Bearer nope"})
        assert bad.status_code == 401
        assert c.get("/v1/models").status_code == 401
        assert c.get("/v1/models", headers={"Authorization": "Bearer secret"}).status_code == 200
        assert c.get("/health").status_code == 200  # system endpoints stay open
        assert c.get("/ready").status_code != 401


def test_studio_auth_guards_ui_and_api(settings: Settings, library: Any) -> None:
    with _client(settings, studio_auth="u:p") as c:
        for path in ("/", "/api/voices"):
            r = c.get(path)
            assert r.status_code == 401, path
            assert r.headers["WWW-Authenticate"].startswith("Basic"), path
            assert c.get(path, auth=("u", "p")).status_code == 200, path
        assert c.get("/health").status_code == 200


# --- §13: UI assets (static part) ----------------------------------------------------


def test_ui_assets_served(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "studio.css" in r.text
    js = client.get("/static/js/core.js")
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]
    assert "export" in js.text


# --- §13 criteria that cannot run in this suite -------------------------------------


@pytest.mark.skip(
    reason="SPEC §13 warm RTF ≤ 0.2 and VRAM ≤ 10 GB with whisper-stt/TEI sharing the card are "
    "3090-host criteria: run scripts/smoke.sh (asserts X-RTF) and the nvidia-smi probe in "
    "docs/RUNBOOK.md §3 on the host; unverifiable against the fake engine (Q-001 / M0)."
)
def test_hardware_rtf_and_vram() -> None:
    """Host probe: X-RTF ≤ 0.2 on a real synthesis (scripts/smoke.sh) and /api/engine
    vram_used_mb ≤ 10240 from nvidia-smi on the shared 3090 (RUNBOOK §3)."""


@pytest.mark.skip(
    reason="SPEC §13 external clients (LiteLLM tts, Open WebUI, Hermes, Wyoming bridge) are "
    "host-side deployments; verified unchanged at cutover (SPEC §11), not in pytest (Q-001 / M0)."
)
def test_acceptance_external_clients_unchanged() -> None:
    """Point LiteLLM at the new /v1 and re-run the four external clients."""


@pytest.mark.skip(
    reason="SPEC §13 spot-check of the eight imported voices by ear against the Qwen3 "
    "versions requires a human listener."
)
def test_acceptance_voice_spot_check() -> None:
    """Human listens to en/ja/ko/zh output of the eight voices."""


@pytest.mark.skip(
    reason="SPEC §13 container build is proven by the CI `package` job, not by pytest."
)
def test_acceptance_container_builds() -> None:
    """podman/docker build of deploy/Containerfile in CI."""


@pytest.mark.skip(
    reason="SPEC §13 UI parity with ui/studio.html (layout, tokens, waveforms) and "
    "keyboard-only navigation need a browser; covered by the Playwright e2e suite."
)
def test_acceptance_ui_parity_and_keyboard() -> None:
    """Playwright screenshots at 1280 px / 390 px vs ui/studio.html + Tab-only walkthrough."""
