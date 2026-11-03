"""Deterministic fake VoxCPM2 engine used by unit/integration/e2e tests."""

from __future__ import annotations

import asyncio
import socket
import struct
import threading
import time
from collections.abc import AsyncGenerator
from typing import Any

import numpy as np
import soundfile as sf
import uvicorn
from app.audio import pcm_s16le, wav_bytes
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

SAMPLE_RATE = 48000


def _tone(seconds: float, note: int = 0, rate: int = SAMPLE_RATE) -> np.ndarray:
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    return (0.2 * np.sin(2 * np.pi * (220 + 40 * note) * t)).astype(np.float32)


def _stream_header(rate: int, audio_format: int, bits: int) -> bytes:
    """44-byte WAV header with unknown sizes, as a streaming engine would send."""
    block = bits // 8
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        0xFFFFFFFF,
        b"WAVE",
        b"fmt ",
        16,
        audio_format,
        1,
        rate,
        rate * block,
        block,
        bits,
        b"data",
        0xFFFFFFFF,
    )


class FakeEngine:
    def __init__(self) -> None:
        # "delay": seconds an input containing "slow" is held. Default 0.25; tests may widen it
        # (M2-T13) so concurrency bursts overlap deterministically. "x_delay" in the request body
        # overrides it per call (fake-only field, ignored by the real engine).
        # "stream_format"/"stream_rate" shape the streamed container (M1-T20): None follows the
        # request's response_format, otherwise "wav16" | "wav_f32" | "pcm16".
        self.state: dict[str, Any] = {
            "calls": 0,
            "active": 0,
            "max_active": 0,
            "last": None,
            "delay": 0.25,
            "stream_format": None,
            "stream_rate": SAMPLE_RATE,
        }
        self.app = self._build()
        self.url = ""
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def _stream(self, payload: dict[str, Any]) -> tuple[bytes, list[bytes]]:
        """(header, audio chunks) for a stream request, honouring response_format."""
        rate = int(self.state.get("stream_rate") or SAMPLE_RATE)
        mode = self.state.get("stream_format") or (
            "wav16" if payload.get("response_format") == "wav" else "pcm16"
        )
        tone = _tone(0.06, abs(hash(str(payload.get("ref_text", "")))) % 4, rate)
        if mode == "wav_f32":
            return _stream_header(rate, 3, 32), [tone.astype("<f4").tobytes()] * 10
        if mode == "pcm16":
            return b"", [pcm_s16le(tone)] * 10
        return _stream_header(rate, 1, 16), [pcm_s16le(tone)] * 10

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
                if "x_delay" in payload:
                    delay = float(payload["x_delay"])
                elif "slow" in str(payload.get("input", "")).lower():
                    delay = float(self.state["delay"])
                else:
                    delay = 0.02
                await asyncio.sleep(delay)
                if payload.get("stream"):

                    async def gen() -> AsyncGenerator[bytes, None]:
                        header, chunks = self._stream(payload)
                        if header:
                            yield header
                        for chunk in chunks:
                            yield chunk

                    return StreamingResponse(gen(), media_type="audio/pcm")
                note = abs(hash(str(payload.get("ref_text", "")))) % 4
                return Response(wav_bytes(_tone(0.6, note), SAMPLE_RATE), media_type="audio/wav")
            finally:
                self.state["active"] -= 1

        @app.get("/debug/state")
        async def debug_state() -> dict[str, Any]:
            return dict(self.state)

        @app.post("/debug/reset")
        async def debug_reset() -> dict[str, str]:
            self.reset()
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
        self.state.update(
            {
                "calls": 0,
                "active": 0,
                "max_active": 0,
                "last": None,
                "delay": 0.25,
                "stream_format": None,
                "stream_rate": SAMPLE_RATE,
            }
        )


def read_wav_duration(data: bytes) -> float:
    import io

    samples, rate = sf.read(io.BytesIO(data), always_2d=True)
    return len(samples) / rate
