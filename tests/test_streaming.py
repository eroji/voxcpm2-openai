"""``/v1/audio/speech`` streaming tests (M1-T2, SPEC §6)."""

from __future__ import annotations

BODY = {"model": "tts-1", "voice": "ashley-en", "input": "hello", "stream": True}


def test_stream_pcm_yields_multiple_chunks(client, library):
    with client.stream("POST", "/v1/audio/speech", json={**BODY, "response_format": "pcm"}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("audio/pcm")
        assert "content-length" not in r.headers
        chunks = [c for c in r.iter_raw() if c]
    assert len(chunks) >= 2
    assert all(len(c) % 2 == 0 for c in chunks)  # s16le frames


def test_stream_wav_has_header_and_rate(client, library):
    with client.stream("POST", "/v1/audio/speech", json={**BODY, "response_format": "wav"}) as r:
        assert r.status_code == 200
        assert r.headers["X-Sample-Rate"] == "48000"
        assert r.headers["X-Voice"].lower() == "ashley-en"
        assert "content-length" not in r.headers
        body = b"".join(r.iter_raw())
    assert body[:4] == b"RIFF"
    assert int.from_bytes(body[24:28], "little") == 48000


def test_stream_mp3_non_empty(client, library):
    with client.stream("POST", "/v1/audio/speech", json={**BODY, "response_format": "mp3"}) as r:
        assert r.status_code == 200
        body = b"".join(r.iter_raw())
    assert len(body) > 0
