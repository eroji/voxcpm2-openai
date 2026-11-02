"""Runnable checks for the spike helpers (no engine needed):
`python scripts/spikes/test_spikes.py` or pytest."""

from __future__ import annotations

import io
import wave

import _client as c
from s6_shim import field_names


def test_helpers() -> None:
    assert c.pcm_duration(b"\0" * (2 * 48000)) == 1.0
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\0" * (2 * 48000))
    assert c.wav_duration(buf.getvalue()) == 1.0
    assert c.wav_duration(b"not a wav") is None
    assert field_names([{"a": {"b": 1}}, {"c": 2}], set()) == {"a", "b", "c"}


if __name__ == "__main__":
    test_helpers()
    print("spike helpers OK")
