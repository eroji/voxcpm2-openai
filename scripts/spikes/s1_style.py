#!/usr/bin/env python3
"""S1 — Style control. Does the engine honour `instructions`, or only the
VoxCPM2 controllable-cloning syntax inside `input`? Decision: keep or drop the
Style field and the `instructions` pass-through (SPEC §12 S1, fallback Q-018)."""

from __future__ import annotations

import _client as c
import httpx


def main() -> None:
    p = c.parse_args(__doc__.splitlines()[0])
    p.add_argument("--instructions", default="calm, level, brisk", help="style instruction to test")
    p.add_argument(
        "--syntax",
        default="({instructions}) {text}",
        help="controllable-cloning syntax template; confirm the exact form against the "
        "upstream VoxCPM2 README before trusting a negative result",
    )
    a = p.parse_args()
    payload = {"model": a.model, "input": a.text, "voice": a.voice, **c.ref_fields(a)}
    if a.audio == str(c.WARMUP_WAV):
        print(
            "note: using the bundled warm-up tone as reference — a real cloned voice "
            "(clip + its transcript) is a better probe of style control."
        )

    # Baseline twice: identical requests that produce different bytes mean the engine is not
    # byte-stable, and every sha comparison below is noise, not signal.
    base1 = c.speech(a.base, a.timeout, **payload)
    base2 = c.speech(a.base, a.timeout, **payload)
    ok = lambda r: r.status_code == 200  # noqa: E731  # used in main flow below
    noise = ok(base1) and ok(base2) and c.sha(base1.content) != c.sha(base2.content)
    instr = c.speech(a.base, a.timeout, instructions=a.instructions, **payload)
    syntax_input = a.syntax.format(instructions=a.instructions, text=a.text)
    syntax = c.speech(a.base, a.timeout, **{**payload, "input": syntax_input})

    base_dur = c.wav_duration(base1.content)
    c.result(
        spike="S1",
        baseline={
            "runs": [
                {
                    "status": r.status_code,
                    "sha": c.sha(r.content),
                    "dur_s": c.wav_duration(r.content),
                }
                for r in (base1, base2)
            ],
            "noise_floor": "runs differ" if noise else "runs identical",
            "dur_s": base_dur,
        },
        instructions={
            "status": instr.status_code,
            "sha": c.sha(instr.content) if ok(instr) else None,
            "dur_s": c.wav_duration(instr.content) if ok(instr) else None,
            "rejected": 400 <= instr.status_code < 500,
        },
        syntax={
            "status": syntax.status_code,
            "sha": c.sha(syntax.content) if ok(syntax) else None,
            "dur_s": c.wav_duration(syntax.content) if ok(syntax) else None,
            "input_sent": syntax_input,
        },
    )
    if not ok(base1):
        print("interpretation: baseline failed — fix connectivity/voice before reading S1.")
        return

    def verdict(r: httpx.Response) -> str:
        if 400 <= r.status_code < 500:
            return f"REJECTED ({r.status_code})"
        if not ok(r):
            return f"failed ({r.status_code})"
        if noise:
            return "accepted; baseline is not byte-stable — listen to compare, do not trust shas"
        same = c.sha(r.content) == c.sha(base1.content)
        return "accepted, output identical" if same else "changed output"

    print(
        f"interpretation: instructions {verdict(instr)}; syntax-in-input {verdict(syntax)}; "
        + ("run-to-run shas differ, so treat any diff as noise until you re-run. " if noise else "")
        + "audio saved to scripts/spikes/out/ — listen to confirm. If both are no-ops, apply the "
        "Q-018 fallback: accept and ignore `instructions`, log once."
    )
    c.save("s1_baseline.wav", base1.content)
    c.save("s1_baseline2.wav", base2.content)
    if ok(instr):
        c.save("s1_instructions.wav", instr.content)
    if ok(syntax):
        c.save("s1_syntax.wav", syntax.content)


if __name__ == "__main__":
    c.guard(main)
