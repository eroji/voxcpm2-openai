"""Audio primitives: decode, resample, normalize, envelope, transcode.

All arrays are float32 mono numpy arrays. ``sample_rate`` is always explicit; the
engine's native output is 48 kHz (SPEC §2).
"""

from __future__ import annotations

import base64
import io
import json
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

MIME_TYPES = {
    "wav": "audio/wav",
    "pcm": "audio/pcm",
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",
    "flac": "audio/flac",
}
EXTENSIONS = {"wav": "wav", "pcm": "pcm", "mp3": "mp3", "opus": "opus", "flac": "flac"}


def mime_for(fmt: str) -> str:
    return MIME_TYPES.get(fmt, "application/octet-stream")


def read_audio(data: bytes) -> tuple[np.ndarray, int]:
    """Decode any soundfile-supported container to mono float32."""
    samples, rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    mono = samples.mean(axis=1) if samples.shape[1] > 1 else samples[:, 0]
    return np.ascontiguousarray(mono, dtype=np.float32), int(rate)


def read_audio_file(path: str | Path) -> tuple[np.ndarray, int]:
    return read_audio(Path(path).read_bytes())


def info_duration_s(data: bytes) -> float:
    """Duration from the container header only — cheap enough to check before a full decode."""
    info = sf.info(io.BytesIO(data))
    return float(info.frames) / float(info.samplerate) if info.samplerate else 0.0


def wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    sf.write(
        buf, np.asarray(samples, dtype=np.float32), sample_rate, format="WAV", subtype="PCM_16"
    )
    return buf.getvalue()


def pcm_s16le(samples: np.ndarray) -> bytes:
    clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


def pcm_s16le_to_float(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def duration_s(samples: np.ndarray, sample_rate: int) -> float:
    if sample_rate <= 0:
        return 0.0
    return float(len(samples) / sample_rate)


def _run_ffmpeg(args: list[str], data: bytes, timeout: float = 60.0) -> bytes:
    try:
        proc = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", *args],
            input=data,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ffmpeg timed out") from exc
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip() or "ffmpeg failed")
    return proc.stdout


def resample(samples: np.ndarray, sample_rate: int, target_rate: int) -> np.ndarray:
    if sample_rate == target_rate:
        return np.ascontiguousarray(samples, dtype=np.float32)
    raw = np.asarray(samples, dtype="<f4").tobytes()
    out = _run_ffmpeg(
        [
            "-f",
            "f32le",
            "-ar",
            str(sample_rate),
            "-ac",
            "1",
            "-i",
            "pipe:0",
            "-ar",
            str(target_rate),
            "-ac",
            "1",
            "-f",
            "f32le",
            "pipe:1",
        ],
        raw,
    )
    return np.frombuffer(out, dtype="<f4").astype(np.float32)


def transcode(
    samples: np.ndarray,
    sample_rate: int,
    fmt: str,
    *,
    target_rate: int | None = None,
    speed: float = 1.0,
) -> bytes:
    """Encode float32 mono to the requested format, resampling/atempo via ffmpeg."""
    rate = target_rate or sample_rate
    if fmt not in MIME_TYPES:
        raise ValueError(f"unsupported format: {fmt}")
    if fmt in ("wav", "pcm") and rate == sample_rate and speed == 1.0:
        return wav_bytes(samples, rate) if fmt == "wav" else pcm_s16le(samples)

    args = ["-f", "f32le", "-ar", str(sample_rate), "-ac", "1", "-i", "pipe:0"]
    if rate != sample_rate:
        args += ["-ar", str(rate)]
    if speed != 1.0:
        factor = min(2.0, max(0.5, speed))
        args += ["-filter:a", f"atempo={factor:.4f}"]
    if fmt == "wav":
        args += ["-f", "wav"]
    elif fmt == "pcm":
        args += ["-f", "s16le"]
    elif fmt == "mp3":
        args += ["-c:a", "libmp3lame", "-b:a", "128k", "-f", "mp3"]
    elif fmt == "opus":
        args += ["-c:a", "libopus", "-b:a", "64k", "-f", "ogg"]
    else:
        args += ["-c:a", "flac", "-f", "flac"]
    args += ["-ac", "1", "pipe:1"]
    return _run_ffmpeg(args, np.asarray(samples, dtype="<f4").tobytes())


def trim_silence(
    samples: np.ndarray, sample_rate: int, *, threshold_db: float = -45.0, pad_ms: int = 50
) -> np.ndarray:
    if samples.size == 0:
        return samples
    threshold = 10 ** (threshold_db / 20.0)
    loud = np.flatnonzero(np.abs(samples) > threshold)
    if loud.size == 0:
        return np.zeros(0, dtype=np.float32)
    pad = int(sample_rate * pad_ms / 1000)
    start = max(0, int(loud[0]) - pad)
    end = min(len(samples), int(loud[-1]) + pad + 1)
    return np.ascontiguousarray(samples[start:end], dtype=np.float32)


def peak_normalize(samples: np.ndarray, *, peak_db: float = -1.0) -> np.ndarray:
    if samples.size == 0:
        return samples
    peak = float(np.max(np.abs(samples)))
    if peak <= 1e-9:
        return samples
    target = 10 ** (peak_db / 20.0)
    return np.ascontiguousarray(samples * (target / peak), dtype=np.float32)


def rms_envelope(samples: np.ndarray, buckets: int = 72) -> list[float]:
    """Return ``buckets`` RMS values normalized to 0..1 (SPEC §7 wave endpoint)."""
    if samples.size == 0:
        return [0.0] * buckets
    edges = np.linspace(0, len(samples), buckets + 1).astype(int)
    out: list[float] = []
    for i in range(buckets):
        chunk = samples[edges[i] : edges[i + 1]]
        out.append(float(np.sqrt(np.mean(np.square(chunk)))) if chunk.size else 0.0)
    peak = max(out) if out else 0.0
    if peak <= 1e-9:
        return [0.0] * buckets
    return [round(min(1.0, v / peak), 4) for v in out]


def to_wav_data_uri(samples: np.ndarray, sample_rate: int) -> str:
    return "data:audio/wav;base64," + base64.b64encode(wav_bytes(samples, sample_rate)).decode()


def decode_data_uri(uri: str) -> tuple[np.ndarray, int]:
    header, _, payload = uri.partition(",")
    if not header.startswith("data:") or ";base64" not in header:
        raise ValueError("not a base64 data URI")
    return read_audio(base64.b64decode(payload))


def is_data_uri(uri: str) -> bool:
    return uri.startswith("data:")


def is_http_url(uri: str) -> bool:
    return uri.startswith(("http://", "https://"))


def write_wav(path: str | Path, samples: np.ndarray, sample_rate: int) -> None:
    sf.write(str(path), np.asarray(samples, dtype=np.float32), sample_rate, subtype="PCM_16")


def json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
