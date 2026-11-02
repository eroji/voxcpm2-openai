#!/usr/bin/env python3
"""S3 — Streaming format. Same text streamed (stream=true, pcm) vs non-streamed
WAV. Decision: sample type/rate handling and trailing-silence trimming for
streams (SPEC §12 S3, Q-003; spec observed 7.69 s streamed vs 5.76 s WAV)."""

from __future__ import annotations

import _client as c


def main() -> None:
    a = c.parse_args(__doc__.splitlines()[0]).parse_args()
    payload = {"model": a.model, "input": a.text, "voice": a.voice, **c.ref_fields(a)}

    wav = c.speech(a.base, a.timeout, response_format="wav", **payload)
    wav.raise_for_status()
    wav_s = c.wav_duration(wav.content)

    status, chunks, headers, err = c.speech_stream(
        a.base, a.timeout, **{**payload, "stream": True, "response_format": "pcm"}
    )
    if status != 200:
        c.result(
            spike="S3",
            wav={"status": wav.status_code, "dur_s": wav_s},
            stream={"status": status, "error": err.decode(errors="replace")[:200]},
        )
        print(
            "interpretation: streamed pcm failed — check the error body; streaming may use a "
            "different response_format value. Record the exact failure in NOTES.md."
        )
        raise SystemExit(1)

    pcm = b"".join(chunk for _, chunk in chunks)
    stream_s = c.pcm_duration(pcm)
    c.save("s3_stream.pcm", pcm)
    c.save("s3_wav.wav", wav.content)
    c.result(
        spike="S3",
        wav={"status": wav.status_code, "dur_s": wav_s, "bytes": len(wav.content)},
        stream={
            "status": status,
            "bytes": len(pcm),
            "dur_s_as_s16le_48k": round(stream_s, 3),
            "first_chunk_ms": round(chunks[0][0], 1) if chunks else None,
            "chunk_count": len(chunks),
            "chunk_sizes": [len(x) for x in chunks[:10]],
            "content_type": headers.get("content-type"),
        },
        duration_delta_s=round(stream_s - (wav_s or 0), 3),
    )
    print(
        f"interpretation: stream decoded as s16le@48k = {stream_s:.2f} s vs WAV {wav_s:.2f} s "
        f"(spec saw 7.69 vs 5.76). If the stream is long, trim trailing silence for non-streamed "
        f"takes and document the measured delta (Q-003); decode the .pcm with "
        f"`ffprobe -f s16le -ar 48000 -i out/s3_stream.pcm` and listen to the tail to confirm."
    )


if __name__ == "__main__":
    c.guard(main)
