#!/usr/bin/env python3
"""S5 — Warm-up. Does one non-streamed + one streamed request after engine
start remove the ~14.5 s first-request delay? Decision: the warm-up sequence in
app/warmup.py (SPEC §12 S5)."""

from __future__ import annotations

import time

import _client as c


def timed(fn) -> tuple[float, object]:
    t0 = time.perf_counter()
    r = fn()
    return time.perf_counter() - t0, r


def main() -> None:
    p = c.parse_args(__doc__.splitlines()[0])
    p.add_argument("--requests", type=int, default=3, help="how many requests to time")
    a = p.parse_args()
    print("run this IMMEDIATELY after restarting the engine (s6-svc -r or container restart).")
    payload = {"model": a.model, "input": a.text, "voice": a.voice}
    if a.audio:
        payload["ref_audio"] = c.data_uri(a.audio)

    timings = []
    for i in range(a.requests):
        stream = i == 1  # second request is streamed, mirroring the planned warm-up
        if stream:
            wall, (status, _, _, _) = timed(
                lambda: c.speech_stream(
                    a.base, a.timeout, **{**payload, "stream": True, "response_format": "pcm"}
                )
            )
        else:
            wall, r = timed(lambda: c.speech(a.base, a.timeout, response_format="wav", **payload))
            status = r.status_code
        timings.append({"n": i + 1, "stream": stream, "status": status, "wall_s": round(wall, 2)})
        print(f"  request {i + 1} ({'stream' if stream else 'wav'}): {wall:.2f} s")

    saved = timings[0]["wall_s"] - timings[1]["wall_s"]
    c.result(spike="S5", timings=timings, seconds_saved=round(saved, 2))
    print(
        f"interpretation: first request cost {timings[0]['wall_s']} s, steady state "
        f"{timings[-1]['wall_s']} s — warm-up saves ~{saved:.1f} s. If request 1 is the slow one, "
        "the planned warm-up (one wav + one stream at startup) is correct; if request 2 is still "
        "slow, add the streamed call to warmup.py explicitly."
    )


if __name__ == "__main__":
    c.guard(main)
