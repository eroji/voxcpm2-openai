"""Deterministic fake VoxCPM2 engine used by unit/integration/e2e tests."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from typing import Any

import numpy as np
import soundfile as sf
import uvicorn
from app.audio import pcm_s16le, wav_bytes
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

SAMPLE_RATE = 48000


def _tone(seconds: float, note: int = 0) -> np.ndarray:
    t = np.linspace(0, seconds, int(SAMPLE_RATE * seconds), endpoint=False)
    return (0.2 * np.sin(2 * np.pi * (220 + 40 * note) * t)).astype(np.float32)


class FakeEngine:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {"calls": 0, "active": 0, "max_active": 0, "last": None}
        self.app = self._build()
        self.url = ""
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def _build(self) -> FastAPI:
        app = FastAPI()

        @app.get("/health")
        async def health() -> dict[str, str]:
            return {"status": "ok"}

        @app.post("/v1/audio/speech")
        async def speech(request: Request) -> Response:
            payload = await request.json()
            self.state["calls"] += 1
            self.state["last"] = payload
            self.state["active"] += 1
            self.state["max_active"] = max(self.state["max_active"], self.state["active"])
            try:
                delay = 0.25 if "slow" in str(payload.get("input", "")).lower() else 0.02
                await asyncio.sleep(delay)
                note = abs(hash(str(payload.get("ref_text", "")))) % 4
                if payload.get("stream"):

                    async def gen():
                        for _ in range(10):
                            yield pcm_s16le(_tone(0.06, note))

                    return StreamingResponse(gen(), media_type="audio/pcm")
                return Response(wav_bytes(_tone(0.6, note), SAMPLE_RATE), media_type="audio/wav")
            finally:
                self.state["active"] -= 1

        @app.get("/debug/state")
        async def debug_state() -> dict[str, Any]:
            return dict(self.state)

        @app.post("/debug/reset")
        async def debug_reset() -> dict[str, str]:
            self.state.update({"calls": 0, "active": 0, "max_active": 0, "last": None})
            return {"status": "reset"}

        return app

    def start(self) -> str:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        config = uvicorn.Config(self.app, host="127.0.0.1", port=port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    self.url = f"http://127.0.0.1:{port}"
                    return self.url
            except OSError:
                time.sleep(0.02)
        raise RuntimeError("fake engine did not start")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)

    def reset(self) -> None:
        self.state.update({"calls": 0, "active": 0, "max_active": 0, "last": None})


def read_wav_duration(data: bytes) -> float:
    import io

    samples, rate = sf.read(io.BytesIO(data), always_2d=True)
    return len(samples) / rate
