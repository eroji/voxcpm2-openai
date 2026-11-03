#!/usr/bin/env python3
"""Backport vllm-project/vllm-omni PR #7010 into vLLM-Omni 0.30.0 (build-time patch).

0.30.0 hard-codes VoxCPM2's CFM solver steps (10) and classifier-free guidance (2.0) in
``voxcpm2_talker.py``. PR #7010 (merged 2026-09-30) reads them from
``hf_overrides.voxcpm2_runtime_config`` instead, as ``inference_timesteps`` and ``cfg_value``,
with the same defaults. This applies that change, so app/engine_runner.py can set them from
``VOXCPM2_INFERENCE_TIMESTEPS`` / ``VOXCPM2_CFG_VALUE``. Values are server-wide and read at
engine start; vLLM-Omni has no per-request field for them (NOTES.md, S4).

Exact-text edits: the build fails if the code drifted, and the script is a no-op once the
installed vLLM-Omni already has the upstream change.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

FIELD_ANCHOR = "    cfg_cutoff_ratio: float = 1.0\n"
FIELD_NEW = (
    "    cfg_cutoff_ratio: float = 1.0\n"
    "    inference_timesteps: int = 10  # backport of vllm-omni PR #7010\n"
    "    cfg_value: float = 2.0  # backport of vllm-omni PR #7010\n"
)

INT_ANCHOR = """            if key in {"audio_emit_every", "vae_decode_every", "batched_fsq_fusion_max_batch"}:
                return max(1, value)
"""
INT_NEW = (
    INT_ANCHOR
    + """            if key == "inference_timesteps":
                # one step with CFG-Zero* skips every estimator call and returns the noise
                return max(2, value)
"""
)

FLOAT_ANCHOR = """            if key == "cfg_cutoff_ratio":
                return min(1.0, max(0.0, float(value)))
"""
FLOAT_NEW = (
    FLOAT_ANCHOR
    + """            if key == "cfg_value":
                cfg = float(value)
                if not math.isfinite(cfg) or cfg <= 0:
                    raise ValueError(f"VoxCPM2 cfg_value must be a positive finite number, got {value!r}")
                return cfg
"""
)

IMPORT_ANCHOR = "import dataclasses\n"
IMPORT_NEW = "import dataclasses\nimport math\n"

TALKER_OLD = """        self._inference_timesteps = 10
        self._cfg_value = 2.0
"""
TALKER_NEW = """        self._inference_timesteps = self._runtime_config.inference_timesteps
        self._cfg_value = self._runtime_config.cfg_value
"""


def _replace(path: Path, edits: list[tuple[str, str]]) -> None:
    text = path.read_text("utf-8")
    for old, new in edits:
        if text.count(old) != 1:
            sys.exit(f"voxcpm2 runtime knobs patch: expected exactly one match in {path}:\n{old}")
        text = text.replace(old, new)
    path.write_text(text, "utf-8")


def main() -> None:
    spec = importlib.util.find_spec("vllm_omni")  # locates the package without importing it
    if spec is None or not spec.submodule_search_locations:
        sys.exit("voxcpm2 runtime knobs patch: vllm_omni is not installed")
    root = Path(next(iter(spec.submodule_search_locations))) / "model_executor/models/voxcpm2"
    runtime, talker = root / "runtime_config.py", root / "voxcpm2_talker.py"

    if "cfg_value" in runtime.read_text("utf-8"):
        print(
            "voxcpm2 runtime knobs: vLLM-Omni already reads cfg_value/inference_timesteps; skipping"
        )
        return
    _replace(
        runtime,
        [
            (IMPORT_ANCHOR, IMPORT_NEW),
            (FIELD_ANCHOR, FIELD_NEW),
            (INT_ANCHOR, INT_NEW),
            (FLOAT_ANCHOR, FLOAT_NEW),
        ],
    )
    _replace(talker, [(TALKER_OLD, TALKER_NEW)])
    print(f"voxcpm2 runtime knobs: patched {runtime} and {talker}")


if __name__ == "__main__":
    main()
