"""Audio primitives: format, resampling and waveform behaviour."""

from __future__ import annotations

import shutil

import numpy as np
import pytest
import soundfile as sf
from app import audio


def tone(seconds: float = 1.0, rate: int = 48000, freq: float = 440.0) -> np.ndarray:
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_wav_roundtrip_keeps_duration_and_rate():
    samples = tone(0.5, 48000)
    decoded, rate = audio.read_audio(audio.wav_bytes(samples, 48000))
    assert rate == 48000
    assert len(decoded) == pytest.approx(len(samples), abs=2)
    assert np.max(np.abs(decoded)) == pytest.approx(0.5, abs=0.01)


def test_pcm_s16le_is_two_bytes_per_sample():
    data = audio.pcm_s16le(tone(0.1))
    assert len(data) == 2 * 4800
    assert np.max(np.abs(audio.pcm_s16le_to_float(data))) == pytest.approx(0.5, abs=0.01)


def test_resample_changes_length_proportionally():
    out = audio.resample(tone(1.0, 48000), 48000, 24000)
    assert len(out) == pytest.approx(24000, abs=32)


def test_rms_envelope_has_72_buckets_in_range():
    amps = audio.rms_envelope(tone(1.0), 72)
    assert len(amps) == 72
    assert all(0.0 <= value <= 1.0 for value in amps)
    assert max(amps) == pytest.approx(1.0)


def test_rms_envelope_of_silence_is_zeros():
    assert audio.rms_envelope(np.zeros(4800, dtype=np.float32), 72) == [0.0] * 72


def test_trim_silence_removes_padding():
    padded = np.concatenate([np.zeros(4800, np.float32), tone(0.5), np.zeros(4800, np.float32)])
    trimmed = audio.trim_silence(padded, 48000)
    assert len(trimmed) < len(padded)
    assert len(trimmed) >= 0.5 * 48000


def test_peak_normalize_hits_minus_one_dbfs():
    out = audio.peak_normalize(tone(0.2))
    assert float(np.max(np.abs(out))) == pytest.approx(10 ** (-1 / 20), abs=0.001)


def test_transcode_mp3_produces_audio_bytes():
    data = audio.transcode(tone(0.2), 48000, "mp3", target_rate=24000)
    assert data[:3] == b"ID3" or data[:1] == b"\xff"


def test_transcode_speed_shortens_duration():
    slow = audio.transcode(tone(1.0), 48000, "wav", target_rate=48000, speed=2.0)
    fast_samples, _ = audio.read_audio(slow)
    assert len(fast_samples) / 48000 == pytest.approx(0.5, abs=0.01)


def test_ffmpeg_run_is_killed_on_timeout():
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    raw = tone(60.0).astype("<f4").tobytes()  # long input: ffmpeg cannot finish in 1 ms
    with pytest.raises(RuntimeError, match="ffmpeg timed out"):
        audio._run_ffmpeg(
            ["-f", "f32le", "-ar", "48000", "-ac", "1", "-i", "pipe:0", "-f", "f32le", "pipe:1"],
            raw,
            timeout=0.001,
        )


def test_info_duration_s_reads_header_without_decode():
    decoded, rate = audio.read_audio(audio.wav_bytes(tone(6.0), 48000))
    assert audio.info_duration_s(audio.wav_bytes(tone(6.0), 48000)) == pytest.approx(
        audio.duration_s(decoded, rate), abs=0.01
    )
    with pytest.raises(sf.LibsndfileError):
        audio.info_duration_s(b"not audio at all")


def test_decode_data_uri_rejects_non_data():
    with pytest.raises(ValueError):
        audio.decode_data_uri("http://example.com/a.wav")
