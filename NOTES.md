# NOTES — M0 spikes (S1–S6)

> **M0 is blocked externally (Q-001):** no VoxCPM2 engine is reachable from the
> development machine, so **no spike has been run**. Implementation proceeds on
> the SPEC §12 documented fallbacks until the scripts in `scripts/spikes/` are
> executed on the `vllm` host (test container `voxcpm2-test`, `127.0.0.1:8881`;
> production container `127.0.0.1:8102`). Nothing in this file is an observed
> result; every table row is a placeholder to be filled in with real output.

## S1 — Style control

- **Status:** not run (requires vllm host)
- **Command:** `python scripts/spikes/s1_style.py --base http://127.0.0.1:8881 --audio app/assets/warmup.wav`
  (baseline runs twice to establish the noise floor; a real cloned-voice clip + `--ref-text` is a
  better probe than the bundled tone)
- **Decision it controls:** whether `/v1/audio/speech` passes `instructions` through and whether
  the Studio Style field stays visible. **Until S1 runs, the Style field stays visible and
  `instructions` keeps being sent.** Only if the spike shows no style support do we hide the field
  and accept-and-ignore `instructions`, logging once (Q-018, SPEC §12 S1) — not before.

| observed | result |
|---|---|
| `instructions` accepted? changes audio? | _pending_ |
| controllable-cloning syntax in `input` changes audio? | _pending_ |

## S2 — Voice design

- **Status:** not run (requires vllm host)
- **Command:** `python scripts/spikes/s2_design.py --base http://127.0.0.1:8881`
  (sends no `ref_audio` on purpose — description-only is what S2 tests)
- **Decision it controls:** whether M4 design candidates go straight through vLLM-Omni or need a
  short-lived `voxcpm` Python worker (extra VRAM to be documented). Fallback until run: isolate
  design behind `app/design.py` assuming the description + `seed` shape (Q-002).

| observed | result |
|---|---|
| description-only (no `ref_audio`) synthesizes? | _pending_ |
| exact accepted payload (saved to `out/s2_payload_seed*.json`) | _pending_ |
| two seeds → different audio? | _pending_ |

## S3 — Streaming format

- **Status:** not run (requires vllm host)
- **Command:** `python scripts/spikes/s3_stream.py --base http://127.0.0.1:8881 --audio app/assets/warmup.wav`
- **Decision it controls:** stream decoding/normalization in `app/engine.py` and trailing-silence
  handling. SPEC observed 7.69 s streamed vs 5.76 s WAV for the same line. Fallback until run:
  treat the stream as s16le mono @ 48 kHz, re-chunk, trim trailing silence only for non-streamed
  takes, document the measured delta (Q-003).

| observed | result |
|---|---|
| stream bytes / seconds as s16le@48k | _pending_ |
| first-chunk ms | _pending_ |
| streamed − WAV duration delta | _pending_ |
| sample type actually emitted (s16le vs float32) | _pending_ |

## S4 — Sampling knobs

- **Status:** not run (requires vllm host)
- **Command:** `python scripts/spikes/s4_knobs.py --base http://127.0.0.1:8881 --audio app/assets/warmup.wav`
- **Decision it controls:** whether `cfg`/`steps`/`seed` are sent per request and whether the UI
  keeps the CFG/steps controls. **Until S4 runs, the CFG/steps controls stay in the UI** and the
  app sends the knobs top-level, retrying once without them on a 4xx and logging once (Q-004).
  Only if the spike shows the knobs are not accepted per request do we remove the controls
  (SPEC §12 S4) — not before.

| observed | result |
|---|---|
| accepted shape (top-level / `extra_body` / `sampling_params` / none) | _pending_ |
| knobs audibly take effect? | _pending_ |

## S5 — Warm-up

- **Status:** not run (requires vllm host)
- **Command:** run `python scripts/spikes/s5_warmup.py --base http://127.0.0.1:8881 --audio app/assets/warmup.wav`
  immediately after an engine restart (`s6-svc -r /run/service/<name>` or `podman restart voxcpm2-test`)
- **Decision it controls:** the warm-up sequence in `app/warmup.py` and `/ready` timing. SPEC
  reports a ~14.5 s first-request delay. Fallback until run: warm-up = one non-streamed + one
  streamed request at startup.

| observed | result |
|---|---|
| request 1 / 2 / 3 wall seconds | _pending_ |
| seconds saved by warm-up | _pending_ |

## S6 — Qwen3 shim field compatibility

- **Status:** not run (requires the `qwen3-tts-openai` container on the vllm host)
- **Command:** `python scripts/spikes/s6_shim.py` — prints
  `podman exec qwen3-tts-openai cat /opt/app/app/openai_api.py` (the host runs Podman; `docker exec`
  only on a Docker host) and the capture checklist; after
  saving request-body fixtures on the host, re-run with `--shim-json <file>` to list field names.
- **Decision it controls:** the accepted-field list of `SpeechRequest` — every field existing
  callers send must be accepted (`task_type` accepted and ignored), and all eight imported voice
  names must keep working (SPEC §6).

| observed | result |
|---|---|
| fields the shim reads (from `openai_api.py`) | _pending_ |
| field names seen in captured fixtures | _pending_ |

## S4 — Sampling knobs: result (2026-11-03, live engine, vLLM-Omni 0.30.0)

Run by hand against `http://vllm.dotslash.io:8881/v1/audio/speech` (voice `ashley`, same text,
`response_format: wav`), comparing SHA-256 of the returned audio:

| request | result |
|---|---|
| `seed 42, cfg 2.0, steps 10` twice | byte-identical (deterministic) |
| `seed 42, cfg 1.0, steps 4` | **byte-identical to cfg 2.0 / steps 10** → `cfg`/`steps` are accepted (200) but ignored |
| `seed 7` | different audio → `seed` is honoured per request |
| no seed, twice | identical to `seed 42` → the default seed is fixed |

- vLLM-Omni reads VoxCPM2's `cfg_value` / `inference_timesteps` only at server start, from
  `hf_overrides.voxcpm2_runtime_config` (vllm-project/vllm-omni PR #7010, merged 2026-09-30;
  before it they were hard-coded 2.0 / 10). There is no per-request field.
- Consequence (SPEC §12 S4): the Speak CFG/steps sliders have no effect, and a per-candidate CFG
  for Design is not possible. A server-wide value needs a deploy-config change and a restart, and
  only works if the running vLLM-Omni build includes PR #7010.
