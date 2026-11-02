# Progress

**Current wave:** 6 — wave-5 review fixes
**Resume:** `scripts/verify.sh` then `/dispatch 1`

## Task table

| Wave | Task | Status | Verify | Notes |
|---|---|---|---|---|
| 0 | scaffold (lead) | pass | `scripts/verify.sh --fast` | contracts, store, audio, speech, fixtures, CI |
| 1 | M0-T1 spikes | pass* | `.venv/bin/python -m compileall -q scripts/spikes` | external host needed for findings |
| 1 | M1-T1 engine client | pass | `pytest tests/test_engine.py` | |
| 1 | M1-T3 s6 + Containerfile | pass | `pytest tests/test_deploy.py` | |
| 1 | M2-T1 importer | pass | `pytest tests/test_importer.py` | |
| 1 | M3-T1 UI shell | pass | `pytest tests/test_ui_assets.py` | |
| 1 | M5-T1 runbook + smoke | pass | `bash -n scripts/smoke.sh` | |
| 5 | M1-T10 warm-up/voice/shutdown fixes (review) | pass | `pytest tests/test_warmup.py` | |
| 5 | M2-T10 store rebuild test (review) | pass | `pytest tests/test_store.py` | |
| 5 | M3-T8 clone gating + status CSS (review) | pass | `pytest tests/test_ui_assets.py` | |
| 5 | M1-T2 `/v1` | pass | `pytest tests/test_v1.py tests/test_streaming.py` | |
| 4 | M1-T9 StreamingSpeech ownership (review) | pass | `pytest tests/test_speech.py` | BLOCKER fix |
| 4 | M2-T9 voice keys + cache cap (review) | pass | `pytest tests/test_store.py` | |
| 4 | M3-T7 screen contract/lifecycle fixes (review) | pass | `pytest tests/test_ui_assets.py` | |
| 4 | M1-T4 warm-up wiring | pass | `pytest tests/test_warmup.py` | |
| 2 | M1-T5 stream admission/lifecycle (review) | pass | `pytest tests/test_stream_lifecycle.py` | BLOCKER fix |
| 2 | M1-T6 container hardening (review) | pass | `pytest tests/test_deploy.py` | |
| 2 | M2-T6 lossless import metadata (review) | pass | `pytest tests/test_store.py tests/test_importer.py` | |
| 2 | M3-T0 UI shell fixes (review) | pass | `pytest tests/test_ui_assets.py` | |
| 2 | M5-T3 runbook/smoke fixes (review) | pass | `bash -n scripts/smoke.sh` | |
| 2 | M2-T2 clone/transcribe | pass | `pytest tests/test_studio_clone.py` | |
| 5 | M2-T3 speak/takes | pass | `pytest tests/test_studio_takes.py` | |
| 2 | M4-T1 design backend | pass | `pytest tests/test_design.py` | |
| 5 | M2-T4 voices CRUD | pass | `pytest tests/test_studio_voices.py` | |
| 5 | M2-T5 engine API | pass | `pytest tests/test_studio_engine.py` | |
| 3 | M1-T7 speech slot/error fixes (review) | pass | `pytest tests/test_speech.py` | BLOCKER fix |
| 3 | M1-T8 container pinning (review) | pass | `pytest tests/test_deploy.py` | |
| 3 | M2-T7 store/importer key safety (review) | pass | `pytest tests/test_store.py tests/test_importer.py` | |
| 3 | M2-T8 clone/STT + timeout (review) | pass | `pytest tests/test_studio_clone.py tests/test_audio.py` | |
| 3 | M3-T2 speak JS | pass | `pytest tests/test_ui_assets.py` | |
| 3 | M3-T3 voices JS | pass | `pytest tests/test_ui_assets.py` | |
| 3 | M3-T4 clone JS | pass | `pytest tests/test_ui_assets.py` | |
| 3 | M3-T5 engine JS | pass | `pytest tests/test_ui_assets.py` | |
| 3 | M4-T2 design JS | pass | `pytest tests/test_ui_assets.py` | |
| 6 | M1-T11 restart/warm-up supervision (review) | wip | `pytest tests/test_queue_http.py` | BLOCKER fix |
| 6 | M1-T12 models/headers/error shape (review) | wip | `pytest tests/test_v1.py` | |
| 6 | M2-T11 take language/48 kHz/FLAC (review) | wip | `pytest tests/test_studio_takes.py tests/test_auth.py` | BLOCKER fix |
| 6 | M3-T9 clone fail-closed + Format hint (review) | wip | `pytest tests/test_ui_assets.py` | |
| 7 | M3-T6 UI e2e | todo | `pytest tests/e2e` | |
| 7 | M5-T2 acceptance | todo | `pytest tests/test_acceptance.py` | |

\* M0-T1 delivers runnable scripts + `NOTES.md`; findings are pending the external host (B-001).

## Wave-1 review outcome (2026-11-02)

- BLOCKER → M1-T5: streamed requests could not return 429/503 (admission after headers) and an
  ffmpeg-encoded stream could leak a slot on disconnect.
- MAJOR → M1-T6: image relied on unverified ffmpeg/curl, unbounded dep upgrades, stale s6 services.
- MAJOR → M2-T6: import lost `created_at`/`file_size` and display casing (test was tautological).
- MAJOR → M3-T0: initial screen `show()` could be skipped, header could not show
  `Not ready · <reason>`, clone copy said 5–20 s (spec: 3–30 s), file picker had no focus ring.
- MAJOR → M5-T3: runbook used docker/Podman wrongly, host GPU index 1, wrong compose data path,
  divergent spike commands, and a smoke loop that masked failures.
- NITs were folded into those five briefs; no inline hotfixes.

## Wave-2 review outcome (2026-11-02)

- BLOCKER → M1-T7: `StreamHandle.aclose()` could skip `_release()` on `CancelledError`; ffmpeg spawn
  failure leaked the engine slot; unknown voice returned 404 instead of the spec's 400-with-names.
- MAJOR → M1-T7: first-audio timing excluded pre-header latency; cfg/steps disabled on unrelated
  4xx; streaming used the output `SAMPLE_RATE` as the engine input rate; transcode errors were
  plain 500s.
- MAJOR → M1-T8: `pydantic-settings` could still upgrade the engine stack; stale-service guard did
  not catch `uvicorn`; reference deploy files had no hash pin.
- MAJOR → M2-T7: import keyed off display name, not `voice_name_lower`; backup filename allowed a
  traversal-shaped metadata key; prune could 500; `file_size` re-encoded every voice.
- MAJOR → M2-T8: malformed STT responses returned plain 500s; upload decode/store blocked the event
  loop; ffmpeg had no timeout.
- NITs folded into those briefs; accepted NITs (design seed collision, `DesignService.settings`
  unused, smoke script needing the real `/v1` routes) are tracked but not blocking.

## Wave-3 review outcome (2026-11-02)

- BLOCKER → M1-T9: `StreamingSpeech` leaked the slot when never iterated (pcm/wav/mp3) and
  `CancelledError` during ffmpeg spawn leaked it too.
- BLOCKER → M3-T7: inline rename sent `new_name` instead of `VoicePatch.name`; STT/API-key state was
  unknowable by the UI (`stt`, `api_keys_required` added to `EngineStatus`).
- MAJOR → M2-T9: display name/key mismatch made imported voices unreachable; uppercase filenames
  listed but not fetchable; unbounded wave cache.
- MAJOR → M3-T7: Generate stayed disabled through playback, stale selected voice after deletion,
  no double-submit guard, stale Save gate, Test-a-line 401 risk with `API_KEYS`, AudioContext/blob
  leaks, design deep-link name validation.
- MAJOR → M1-T9: flaky truncating-server test (`test_transport_error_mid_body`); ref_audio data URI
  unbounded; duplicated 48 kHz constant.
- Accepted NITs: engine stat sub-lines, voices filtered count label, `prompt()` for save-as-voice,
  success text styling on clone (fixed in M3-T7).

## Wave-4 review outcome (2026-11-02)

- No BLOCKERs. MAJORs fixed in wave 5: clone gating read a not-yet-loaded engine status and could
  be skipped on a voices error (M3-T8); `.vs-status` had no CSS (M3-T8); ffmpeg-disconnect cleanup
  path lacked a test (folded into M1-T10's test scope); warm-up stream-failure ordering and
  `run_forever` branches were untested (M1-T10); display-name lookups returned `invalid_voice_name`
  without the valid-name list (M1-T10); index-rebuild test never rebuilt (M2-T10).
- Added Q-023 (Test-a-line vs `API_KEYS`): UI-only gating, human confirmation requested.

## Wave-5 review outcome (2026-11-02)

- BLOCKER → M1-T11: `/api/engine/restart` warmed the old process and `run_forever` never re-warmed
  after a crash, so `/ready` could be 503 forever; `/ready` also ignored engine liveness. No
  HTTP-level 429 test existed.
- BLOCKER → M2-T11: streamed FLAC takes were never saved (libsndfile cannot decode pipe-FLAC) even
  though `X-Take-Id` was returned.
- MAJOR → M2-T11: take language used display name; internal takes were not always 48 kHz;
  full-audio work on the event loop; auth untested.
- MAJOR → M1-T12: models aliases missing, `model` optional, non-ASCII `X-Voice` could 500,
  404/405 not OpenAI-shaped.
- MAJOR → M3-T9: Test-a-line failed open with unknown engine status; `#clone-notes` overflow;
  Studio Format inert (Q-024).
- Accepted NITs: CSRF on Basic-auth POSTs (LAN-only, spec mandates Basic), event-loop list/wave
  cost for large libraries, header `X-Take-Id` before persistence (Q-006).

## Blockers

| ID | Blocker | Owner | Unblocks |
|---|---|---|---|
| B-001 | M0 spikes need the `vllm` host with the VoxCPM2 engine; none reachable here | human | M0 close, S1–S4 UI decisions |
| B-002 | `STT_URL` exact URL for whisper-stt unverified (Q-005) | human | clone auto-transcribe |
| B-003 | Harbor CI credentials + runner docker capability unverified (Q-015) | human | `package` job real build |
| B-004 | Design backend path depends on spike S2 (Q-002); default implemented behind one module | human | M4 close confidence |

## Decisions log (append-only, newest last)

- **2026-11-02 —** Adopted `SPEC.md` as sole authority; `ui/**` and validated deploy YAML are
  read-only. Python 3.12 + FastAPI + soundfile/numpy + ffmpeg; plain ES modules for the UI (no
  build step).
- **2026-11-02 —** Engine client owns queueing/metrics so `/v1` and Studio share one capacity
  model (2 active, 16 queued, then 429; Q-016).
- **2026-11-02 —** M0 cannot run locally (no engine, wrong GPU host). Spike scripts + `NOTES.md`
  are deliverables; M0 stays `blocked (external)` until run on `vllm` (Q-001).
- **2026-11-02 —** Streaming headers/duration: `X-Audio-Duration` and `X-RTF` only on non-streamed
  responses, per SPEC §6; streams expose `X-Voice`/`X-Sample-Rate`.
- **2026-11-02 —** `/api/speak` streaming returns audio with `X-Take-Id`; the take is persisted
  when the stream completes (Q-006). Design candidates are takes with `kind="design"` (Q-007).
- **2026-11-02 —** RTF is wall-time / audio-duration (SPEC reports 0.14); `first_audio_ms` is
  recorded for streamed responses only. `speed` is never sent to the engine (native control
  unverified, S4) — ffmpeg `atempo` handles non-streamed output, streams ignore it per §6.
- **2026-11-02 —** `EngineStatus.reason` added (lead contract change) so the header can render the
  spec's third state `Not ready · <reason>` from warm-up errors.
- **2026-11-02 —** `EngineClient.stream()` replaced by `open_stream()` returning `StreamHandle`:
  admission (queue + readiness) must happen before HTTP headers (wave-1 BLOCKER).
- **2026-11-02 —** Warm-up reference when the library is empty is a bundled 1.5 s tone
  (`app/assets/warmup.wav`), clearly a placeholder for non-speech warm-up only (Q-014).

## Next action

Dispatch wave 6 (four parallel workers): M1-T11, M1-T12, M2-T11, M3-T9.
