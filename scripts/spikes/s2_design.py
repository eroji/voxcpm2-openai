#!/usr/bin/env python3
"""S2 — Voice design. Can a description-only request (no ref_audio) synthesize,
and do different seeds give different speakers? Decision: whether M4 design can
go through vLLM-Omni or needs the `voxcpm` Python worker fallback (SPEC §12 S2,
Q-002)."""

from __future__ import annotations

import json

import _client as c
import httpx


def try_payload(a, payload: dict) -> tuple[httpx.Response, dict]:
    """Send payload without `voice` first; if the endpoint 4xxes on the missing
    field, retry once with voice=<--voice> and report which shape was accepted."""
    r = c.speech(a.base, a.timeout, **payload)
    if 400 <= r.status_code < 500:
        r2 = c.speech(a.base, a.timeout, **{**payload, "voice": a.voice})
        return (r2, {**payload, "voice": a.voice}) if r2.status_code == 200 else (r, payload)
    return r, payload


def main() -> None:
    p = c.parse_args(__doc__.splitlines()[0])
    p.add_argument(
        "--description",
        default="a warm middle-aged woman with a calm, clear speaking voice",
        help="voice description sent as `instructions`",
    )
    p.add_argument("--seed", type=int, default=1, help="first seed (second run uses seed+1)")
    a = p.parse_args()

    runs = []
    for seed in (a.seed, a.seed + 1):
        payload = {"model": a.model, "input": a.text, "instructions": a.description, "seed": seed}
        r, accepted = try_payload(a, payload)
        runs.append(
            {
                "seed": seed,
                "status": r.status_code,
                "sha": c.sha(r.content) if r.status_code == 200 else None,
                "dur_s": c.wav_duration(r.content) if r.status_code == 200 else None,
                "error": c.err_snippet(r) if r.status_code >= 400 else None,
                "payload": accepted,
            }
        )
        if r.status_code == 200:
            c.save(f"s2_seed{seed}.wav", r.content)
            c.save(f"s2_payload_seed{seed}.json", json.dumps(accepted, indent=2).encode())

    both_ok = all(x["status"] == 200 for x in runs)
    c.result(
        spike="S2",
        runs=runs,
        accepted_payload=runs[0]["payload"] if runs[0]["status"] == 200 else None,
        seeds_differ=both_ok and runs[0]["sha"] != runs[1]["sha"],
    )
    if not both_ok:
        print(
            "interpretation: description-only synthesis failed — voice design cannot go through "
            "vLLM-Omni as-is; plan the `voxcpm` Python worker fallback and document its extra VRAM "
            "(SPEC §12 S2)."
        )
    elif runs[0]["sha"] == runs[1]["sha"]:
        print(
            "interpretation: accepted but seeds produced identical audio — seed is not honoured "
            "per request; vary candidates another way or use the worker fallback (Q-002)."
        )
    else:
        print(
            "interpretation: voice design works through the engine and seeds differ — M4 can call "
            "/v1/audio/speech directly; the exact accepted payload is saved in scripts/spikes/out/."
        )


if __name__ == "__main__":
    c.guard(main)
