# Progress

**Current wave:** 14 — SPEC v0.2.0 complete
**Resume:** `scripts/verify.sh` then `/status`; next human action is `git push` to run GitLab CI

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
| 6 | M1-T11 restart/warm-up supervision (review) | pass | `pytest tests/test_queue_http.py` | BLOCKER fix |
| 6 | M1-T12 models/headers/error shape (review) | pass | `pytest tests/test_v1.py` | |
| 6 | M2-T11 take language/48 kHz/FLAC (review) | pass | `pytest tests/test_studio_takes.py tests/test_auth.py` | BLOCKER fix |
| 6 | M3-T9 clone fail-closed + Format hint (review) | pass | `pytest tests/test_ui_assets.py` | |
| 7 | M3-T6 UI e2e | pass | `pytest tests/e2e` | |
| 8 | M1-T13 CI/auth/500 fixes (review) | pass | `pytest tests/test_config.py tests/test_auth.py` | BLOCKER fix |
| 8 | M1-T14 models/instructions/headers (review) | pass | `pytest tests/test_v1.py tests/test_engine.py` | |
| 8 | M1-T15 speech off-loop (review) | pass | `pytest tests/test_speech.py` | |
| 8 | M2-T12 store/take invariants (review) | pass | `pytest tests/test_store.py tests/test_studio_takes.py` | |
| 8 | M2-T13 deterministic burst + host RTF (review) | pass | `pytest tests/test_acceptance.py` | |
| 8 | M3-T10 UI release e2e (review) | pass |
| 11 | M2-T15a engine log runner | pass | |
| 11 | M3-T12 startup asset + Engine UI | pass | |
| 12 | M2-T15b startup API/routing | pass | |
| 13 | M2-T16 startup state/gate fixes | pass | |
| 13 | M2-T17 runner log/backoff | pass | |
| 13 | M3-T13 startup UX/e2e | pass |
| 14 | M2-T18 remove Qwen3 import | pass | |
| 14 | M1-T18 empty library + warm-only | pass | |
| 14 | M3-T14 presets/empty/icons/log table | pass | | `pytest tests/e2e` | |
| 7 | M5-T2 acceptance | pass | `pytest tests/test_acceptance.py` | |

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

## Release-review outcome (2026-11-02)

- BLOCKER → M1-T13: CI Alpine/node jobs inherited the Debian `before_script`; `package` could be
  green without an image; malformed `STUDIO_AUTH`/`API_KEYS` failed open; unhandled errors were
  plain-text 500s; `/docs` was open and loaded a CDN.
- BLOCKER → M1-T14: `/v1/models` listed three cards against SPEC §6; `instructions` had no
  unsupported-field fallback; upstream 429/503 became 502; `X-Voice` allowed CR/LF.
- MAJOR → M1-T15: non-stream decode/transcode ran on the event loop.
- MAJOR → M2-T12: voice listing decoded every audio tensor on each poll; same-second backups
  overwrote; design takes leaked into Speak; take rate not pinned to 48 kHz.
- MAJOR → M2-T13: burst test timing hazard; RTF only proven on the fake engine; runbook queue probe
  ignored auth.
- MAJOR → M3-T10: disabled controls looked enabled; streaming/clone-save paths had no browser test;
  only two 390 px screenshots.
- Human decisions 2026-11-02: Q-023 resolved (Studio previews use `/api/speak?save=false` under
  Studio auth); Q-024 resolved (Format drives preview + take playback); `STT_URL` set to
  `http://vllm.dotslash.io:8890/v1`; push approved; CI/CD aligned with the `notely` house pattern.

## Final local verification (2026-11-02)

```
scripts/verify.sh
  format check     54 files already formatted
  lint             All checks passed
  typecheck        Success: no issues found in 21 source files
  unit+integration 344 passed, 5 skipped in 37.30s
  coverage         93.45% (gate 65%)
  e2e              29 passed (real Chromium)
  python build     sdist + wheel built
  package contract Containerfile + s6 checks passed
==> OK
```

Skipped, by design: five SPEC §13 hardware/external checks (`test_hardware_rtf_and_vram`,
LiteLLM/Open WebUI/Hermes/Wyoming, ear spot-check, container build, UI visual parity note). M0
spikes remain host-run. CI has not run yet: the branch is local (47+ commits ahead of origin).

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

- **2026-11-02 —** Push approved; CI/CD aligned with `notely` (`HARBOR_TOKEN`, `GIT_DEPTH: 0`,
  `DOCKER_TLS_CERTDIR: ""`, tag releases, base64 SSH key, target-side docker login). CD remains
  opt-in via `DEPLOY_TARGET`.
- **2026-11-02 —** Studio synthesis moved to `/api/speak?save=false`; Test-a-line works with
  `API_KEYS` set and Studio Format controls the preview/take playback container.

- **2026-11-02 —** Human scope change: only `vX.Y.Z` tags build (no branch/commit pipelines);
  deploy is opt-in from those tags. Images are tagged `vX.Y.Z` and `latest`.
- **2026-11-02 —** `deploy/compose.yaml` now pins the current release image `:v0.1.0` (human
  request); only the image tag may change there, `voxcpm2-3090.yaml` stays frozen.
- **2026-11-02 —** Startup bug found: the qwen3 base image runs its engines from a runtime
  `S6_STAGE2_HOOK` plus `start.sh` ENTRYPOINT, not static s6-rc dirs. Inheriting them started
  `base`/Qwen3-TTS. Containerfile now removes `/opt/app`, sets `S6_STAGE2_HOOK=/bin/true` and
  `ENTRYPOINT ["/init"]`, and a test fails if that regresses. Compose also passes `HF_TOKEN`
  (host env) and allows `HF_HUB_OFFLINE=0`.
- **2026-11-02 —** First tag build failed before `docker build`: `HARBOR_USER` was empty (CI
  variables are likely marked protected while the tag is unprotected). The login step now fails
  with that explanation instead of a raw docker error. Checks/tests/e2e
  are not repeated in CI; `scripts/verify.sh` is the quality gate. Image tags: short SHA on `main`,
  `X.Y.Z` on `vX.Y.Z` tags.

- **2026-11-03 —** Startup page feature (`ui/STARTING.md`) implemented: `/` serves the startup
  page until warm, `/api/engine/startup` returns the 4-step contract, `/api/*` is gated with the
  two documented exceptions, engine logs tee to `/data/logs/engine.log`, s6 backoff
  30/60/120 s with a down marker, and failures map to plain-language text. Real-image check:
  startup page at `/`, `/api/voices` 503 + Retry-After 5, `/health` 200, `/ready` 503.

- **2026-11-03 —** Release v0.1.3: startup page, engine log runner + backoff, updated Engine UI,
  Studio preview/format, HF token passthrough, container supervisor fixes.

- **2026-11-03 —** SPEC v0.2.0: Qwen3 import removed (code, route, compose mount, docs); empty-library
  guard (400 `no_voices`, UI notices, disabled Generate); test-phrase presets served from
  `/static/presets.json`; app icons; startup log table; warm-only synthesis and metrics exclusion
  for pre-ready requests.

- **2026-11-03 —** Release v0.2.0.

- **2026-11-03 —** Streams now request the engine WAV, strip/parse the header, convert float32,
  advertise the real rate and resample to the requested rate (fixes garbled streaming). Voice
  design/style use VoxCPM2’s `(control instruction)text` syntax. Takes are session-scoped
  (`X-Studio-Session`), tabs no longer reflow, take playback gets pause/progress/hover states, the
  Voices editor is full-width with auto-transcribe, Download matches Save as voice, and the clone
  button says “Transcribe with STT”.

- **2026-11-03 —** Release v0.3.0: languages management, stream format/rate fix, design control
  syntax, session-scoped takes, player/editor UX.

- **2026-11-03 —** Deploy is automatic on `vX.Y.Z` tags: runner → root@vllm (direct, deploy user),
  writes `VOXCPM2_IMAGE` to `/opt/voxcpm2-openai/.env`, copies `deploy/compose.yaml`, recreates the
  pod with `podman-compose --force-recreate --pull`, waits for `/ready`. Host was updated to
  `v0.3.0` and is healthy. `DEPLOY_SSH_KEY` must be authorized for `deploy@vllm` (key + scoped podman sudo configured).

- **2026-11-03 —** Release v0.3.1: automatic tag deploy to vllm via jumpbox, .env image tag.

- **2026-11-03 —** Deploy corrected to direct `root@vllm` with `DEPLOY_SSH_KEY` (jumpbox removed);
  release v0.3.2.

- **2026-11-03 —** Deploy job now mirrors notely: `deploy@vllm.dotslash.io` with `DEPLOY_SSH_KEY`,
  Harbor login, `.env` update, `sudo -n podman-compose` recreate; release v0.3.3.

- **2026-11-03 —** Found the actual streaming bug: vLLM-Omni defaults `stream=true` to SSE
  (`speech.audio.delta`), so the app relayed SSE text as PCM. `stream_format=audio` now forces raw
  WAV; verified directly against the engine (first bytes `RIFF`). Player state was cleared by the
  first `timeupdate`; `attachPlayer` now only changes state on play/pause/stop. Speak and Design
  share it; Download/editor alignment fixed.

- **2026-11-03 —** Static assets now revalidate (`no-cache`) so a redeploy cannot be masked by
  cached JS/CSS; design previews normalized to -1 dBFS; candidate count ordered 1–4. Release
  v0.3.5.

- **2026-11-03 —** Startup log is now column-free messages with ERROR/WARN highlighting; Design
  selection is a background row highlight (no border/colour shift); candidate counts 1–4; design
  previews normalized; Speak no longer offers Save as voice. Release v0.3.6.

- **2026-11-03 —** Streamed takes hand off to the new take's shared player (pause icon, row
  highlight, waveform playhead); a paused take keeps its playhead (grey past it, even on the newest
  take); Voices table uses fixed column widths so the inline editor never reflows or clips rows.
  Release v0.3.7.

- **2026-11-03 —** Voices editor drops the accent focus outline (field rule turns solid instead);
  Source column is plain text without the square marker; Speak Style control and Design
  Description persist in browser localStorage across reloads, restarts and redeploys.
  Release v0.3.8.

- **2026-11-03 —** Voices editor keeps every first line in place and only the transcript cell
  slides down; takes and candidate rows get horizontal padding; streamed playback buffers 0.3 s
  (and re-buffers on underrun) instead of stuttering, and the stream→take handoff suspends the
  stream before the take plays (no overlap). Release v0.3.9.

## Next action

1. `git push origin main` and watch the GitLab pipeline (`checks`, `javascript`, `test`, `e2e`,
   `build`, `package:check`; `package` runs when `HARBOR_USER`/`HARBOR_PASSWORD` are set).
2. Run M0 spikes on the `vllm` host (`scripts/spikes/README.md`) and append results to `NOTES.md`.
3. Remaining human inputs: Q-015 (runner docker/Harbor creds for `package`/CD), Q-014 (real
   warm-up clip). `STT_URL` is now documented as `http://vllm.dotslash.io:8890/v1`.
