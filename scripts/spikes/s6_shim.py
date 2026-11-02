#!/usr/bin/env python3
"""S6 — Qwen3 shim field compatibility. No container introspection is possible
from a worker machine: this script prints the exact command to run on the `vllm`
host plus the capture checklist, and parses field names from request fixtures
you save afterwards (--shim-json). Decision: the accepted-field list in
app/models.py / SpeechRequest (SPEC §6, §12 S6)."""

from __future__ import annotations

import json
from pathlib import Path

import _client as c

CAT_CMD = "podman exec qwen3-tts-openai cat /opt/app/app/openai_api.py"
CHECKLIST = [
    "every request body field the shim reads (request.json()/Body(...) — note defaults)",
    "how `preset` / `clone` / `design` task types map to engines and voice naming",
    "the exact `model` values callers send",
    "response content types and any custom headers the shim sets",
    "error shape and status codes callers may depend on",
    "auth expectations (any Bearer-key check on the shim side)",
]


def field_names(obj: object, acc: set[str]) -> set[str]:
    if isinstance(obj, dict):
        acc.update(obj)
        for v in obj.values():
            field_names(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            field_names(v, acc)
    return acc


def main() -> None:
    p = c.parse_args(__doc__.splitlines()[0])
    p.add_argument(
        "--shim-json",
        action="append",
        default=[],
        metavar="PATH",
        help="saved request fixture (JSON object, or list of them); repeatable",
    )
    a = p.parse_args()

    print(f"read the shim source on the vllm host with:\n  {CAT_CMD} > shim_openai_api.py")
    print("(the vllm host runs Podman; use `docker exec …` only on a Docker host)")
    print("capture, from that file and its callers' traffic:")
    for item in CHECKLIST:
        print(f"  - {item}")

    fields: set[str] = set()
    for path in a.shim_json:
        fields |= field_names(json.loads(Path(path).read_text()), set())
    if fields:
        c.result(spike="S6", fixtures=a.shim_json, request_fields=sorted(fields))
        print(
            "interpretation: ensure SpeechRequest accepts (and ignores what it must) every field "
            "above; task_type must be accepted and ignored (SPEC §6)."
        )
    else:
        c.result(spike="S6", fixtures=[], request_fields=None)
        print(
            "interpretation: no fixtures parsed — capture request bodies on the host first, "
            "then rerun with --shim-json. Nothing is decided yet."
        )


if __name__ == "__main__":
    c.guard(main)
