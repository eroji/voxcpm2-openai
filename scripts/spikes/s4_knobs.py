#!/usr/bin/env python3
"""S4 — Sampling knobs. Which payload shape carries cfg/steps/seed to the
engine? Decision: whether the UI keeps the CFG/steps controls and how the app
sends them (SPEC §12 S4, Q-004: send top-level, retry once without on 4xx)."""

from __future__ import annotations

import _client as c

KNOBS = {"cfg": 2.0, "steps": 10, "seed": 42}


def main() -> None:
    a = c.parse_args(__doc__.splitlines()[0]).parse_args()
    base_payload = {"model": a.model, "input": a.text, "voice": a.voice, **c.ref_fields(a)}

    shapes = {
        "top_level": base_payload | KNOBS,
        "extra_body": base_payload | {"extra_body": KNOBS},
        "sampling_params": base_payload | {"sampling_params": KNOBS},
    }
    accepted = None
    results = {}
    for name, payload in shapes.items():
        r = c.speech(a.base, a.timeout, **payload)
        results[name] = {
            "status": r.status_code,
            "sha": c.sha(r.content) if r.status_code == 200 else None,
            "error": c.err_snippet(r) if r.status_code >= 400 else None,
        }
        if r.status_code == 200 and accepted is None:
            accepted = name
            c.save(f"s4_{name}.wav", r.content)
        if accepted:  # stop at the first accepted shape
            break

    c.result(spike="S4", accepted_shape=accepted, attempts=results)
    if accepted is None:
        print(
            "interpretation: no shape carried the knobs — the engine ignores or rejects them. "
            "Apply Q-004: send top-level, retry once without on 4xx, log once; remove the CFG/"
            "steps controls from the UI (SPEC §12 S4)."
        )
    else:
        print(
            f"interpretation: `{accepted}` is accepted — use that shape in app/engine.py and keep "
            "the UI controls. Re-run twice with different `seed` values in KNOBS and compare the "
            "sha to prove the knobs take effect, not just parse."
        )


if __name__ == "__main__":
    c.guard(main)
