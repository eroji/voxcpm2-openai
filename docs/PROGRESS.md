# Progress

**Current wave:** 0 — scaffold (lead)
**Resume:** `scripts/verify.sh` then `/dispatch 1`

## Task table

| Wave | Task | Status | Verify | Notes |
|---|---|---|---|---|
| 0 | scaffold (lead) | pass | `scripts/verify.sh --fast` | contracts, store, audio, speech, fixtures, CI |
| 1 | M0-T1 spikes | todo | `.venv/bin/python -m compileall -q scripts/spikes` | external host needed for findings |
| 1 | M1-T1 engine client | todo | `pytest tests/test_engine.py` | |
| 1 | M1-T3 s6 + Containerfile | todo | `pytest tests/test_deploy.py` | |
| 1 | M2-T1 importer | todo | `pytest tests/test_importer.py` | |
| 1 | M3-T1 UI shell | todo | `pytest tests/test_ui_assets.py` | |
| 1 | M5-T1 runbook + smoke | todo | `bash -n scripts/smoke.sh` | |
| 2 | M1-T2 `/v1` | todo | `pytest tests/test_v1.py tests/test_streaming.py` | |
| 2 | M1-T4 warm-up wiring | todo | `pytest tests/test_warmup.py` | |
| 2 | M2-T2 clone/transcribe | todo | `pytest tests/test_studio_clone.py` | |
| 2 | M2-T3 speak/takes | todo | `pytest tests/test_studio_takes.py` | |
| 2 | M4-T1 design backend | todo | `pytest tests/test_design.py` | |
| 3 | M2-T4 voices CRUD | todo | `pytest tests/test_studio_voices.py` | |
| 3 | M2-T5 engine API | todo | `pytest tests/test_studio_engine.py` | |
| 3 | M3-T2 speak JS | todo | `pytest tests/test_ui_assets.py` | |
| 3 | M3-T3 voices JS | todo | `pytest tests/test_ui_assets.py` | |
| 3 | M3-T4 clone JS | todo | `pytest tests/test_ui_assets.py` | |
| 3 | M3-T5 engine JS | todo | `pytest tests/test_ui_assets.py` | |
| 3 | M4-T2 design JS | todo | `pytest tests/test_ui_assets.py` | |
| 4 | M3-T6 UI e2e | todo | `pytest tests/e2e` | |
| 4 | M5-T2 acceptance | todo | `pytest tests/test_acceptance.py` | |

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
- **2026-11-02 —** Warm-up reference when the library is empty is a bundled 1.5 s tone
  (`app/assets/warmup.wav`), clearly a placeholder for non-speech warm-up only (Q-014).

## Next action

Dispatch wave 1 (six parallel workers): M0-T1, M1-T1, M1-T3, M2-T1, M3-T1, M5-T1.
