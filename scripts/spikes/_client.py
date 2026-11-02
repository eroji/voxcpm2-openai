"""Shared helper for the M0 spike scripts (SPEC §12).

Real requests only — if the engine is unreachable the script exits with an error,
it never fabricates a result. Run on the `vllm` host against the test container
(`http://127.0.0.1:8881`), or `http://127.0.0.1:8102` inside the production
container. Request shape follows docs/CONTRACTS.md §5.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import sys
import time
import wave
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parent / "out"
WARMUP_WAV = Path(__file__).resolve().parents[2] / "app" / "assets" / "warmup.wav"


def parse_args(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument(
        "--base",
        default="http://127.0.0.1:8881",
        help="engine base URL (test container on the vllm host; :8102 inside prod container)",
    )
    p.add_argument("--voice", default="alison-en", help="voice label sent in the `voice` field")
    p.add_argument(
        "--audio",
        default=str(WARMUP_WAV),
        help="reference audio sent as ref_audio (default: the bundled 1.5 s warm-up tone; "
        "a real 5-10 s cloned voice is a far better probe — the tone only proves plumbing)",
    )
    p.add_argument(
        "--ref-text",
        default="Warm-up.",
        help="transcript of --audio, sent as ref_text (Q-014: the warm-up tone pairs with "
        '"Warm-up."; with a real voice clip pass its actual words)',
    )
    p.add_argument(
        "--text",
        default="The quick brown fox jumps over the lazy dog.",
        help="text to speak",
    )
    p.add_argument("--model", default="voxcpm2", help="value sent in the `model` field")
    p.add_argument("--timeout", type=float, default=180.0, help="per-request timeout in seconds")
    return p


def ref_fields(a: argparse.Namespace) -> dict:
    """ref_audio + ref_text for every synthesis request. The raw VoxCPM2 engine has no voice
    library, so a request without a reference synthesizes nothing worth comparing; S2 is the
    deliberate exception (it tests description-only)."""
    path = Path(a.audio)
    if not path.is_file():
        sys.exit(f"--audio {path} not found; pass a real reference clip or omit the flag")
    return {"ref_audio": data_uri(path), "ref_text": a.ref_text}


def data_uri(path: str) -> str:
    """Encode a local audio file as a base64 data URI (mime is irrelevant to the engine)."""
    return "data:;base64," + base64.b64encode(Path(path).read_bytes()).decode()


def speech(base: str, timeout: float, **payload: object) -> httpx.Response:
    """Non-streamed POST to /v1/audio/speech. Returns the full response."""
    return httpx.post(f"{base}/v1/audio/speech", json=payload, timeout=timeout)


def speech_stream(
    base: str, timeout: float, **payload: object
) -> tuple[int, list[tuple[float, bytes]], dict[str, str], bytes]:
    """Streamed POST to /v1/audio/speech.

    Returns (status, [(elapsed_ms, chunk_bytes), ...], headers, error_body).
    error_body is non-empty only on a non-200 response.
    """
    chunks: list[tuple[float, bytes]] = []
    t0 = time.perf_counter()
    with httpx.Client(timeout=timeout) as client:
        with client.stream("POST", f"{base}/v1/audio/speech", json=payload) as r:
            if r.status_code != 200:
                body = r.read()
                return r.status_code, [], dict(r.headers), body
            for raw in r.iter_raw():
                if raw:
                    chunks.append(((time.perf_counter() - t0) * 1000.0, raw))
            return r.status_code, chunks, dict(r.headers), b""


def save(name: str, data: bytes) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    path.write_bytes(data)
    return path


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def wav_duration(data: bytes) -> float | None:
    """Seconds of a WAV payload, or None if it is not readable WAV."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            return w.getnframes() / w.getframerate()
    except Exception:
        return None


def pcm_duration(data: bytes, rate: int = 48000) -> float:
    """Seconds of raw s16le mono PCM at `rate`."""
    return len(data) / 2 / rate


def result(**fields: object) -> None:
    """Print the one-line JSON result the brief asks for."""
    print(json.dumps(fields, default=str))


def guard(main_fn) -> None:
    """Run main(); on any transport error print an honest failure and exit 2."""
    try:
        main_fn()
    except httpx.HTTPError as e:
        print(f"engine unreachable at the given --base: {e}", file=sys.stderr)
        sys.exit(2)


def err_snippet(r: httpx.Response, limit: int = 200) -> str:
    """First `limit` chars of an error response body, for the JSON output."""
    return r.text[:limit]
