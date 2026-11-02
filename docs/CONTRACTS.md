# Contracts (frozen during waves)

Everything here is implemented in the wave-0 scaffold or explicitly frozen as an interface. If a
task needs a change, it reports `BLOCKED` and the lead amends this file in a new task.

## 1. Modules

| Module | Contract |
|---|---|
| `app/config.py` | `Settings` (pydantic-settings). Env names = field names upper-cased: `DATA_DIR`, `ENGINE_URL`, `ENGINE_CONCURRENCY`, `MAX_QUEUE`, `API_KEYS`, `STUDIO_AUTH`, `STT_URL`, `SAMPLE_RATE`, `TAKE_RETENTION`, `ALLOW_REMOTE_REF_AUDIO`, … `settings.engine_base` strips a trailing `/v1`. |
| `app/models.py` | `SpeechRequest`, `TakeInfo`, `VoiceSummary`, `DesignRequest`, `CandidateInfo`, `EngineStatus`, … Pydantic, `extra="ignore"`. |
| `app/errors.py` | `APIError` subclasses `BadRequest`(400), `Unauthorized`(401), `Forbidden`(403), `NotFound`(404), `Conflicting`(409), `QueueFull`(429+Retry-After), `EngineNotReady`(503+Retry-After), `EngineError`(502). Body is OpenAI shape. |
| `app/audio.py` | `read_audio(bytes)→(float32 mono, rate)`, `wav_bytes`, `pcm_s16le`, `pcm_s16le_to_float`, `duration_s`, `resample`, `transcode(samples, rate, fmt, target_rate=, speed=)`, `trim_silence`, `peak_normalize`, `rms_envelope(samples, 72)`, `to_wav_data_uri`, `decode_data_uri`, `is_data_uri`, `is_http_url`, `mime_for`. |
| `app/store.py` | `VoiceStore(settings)` and `TakeStore(settings)`. See §4. |
| `app/engine.py` | `EngineClient` see §5. |
| `app/speech.py` | `SpeechService(store, engine, settings)`; `synthesize(req)->SpeechResult`, `stream(req)->StreamingSpeech`, `resolve(req)->ResolvedVoice`. |
| `app/importer.py` | `import_qwen3_speakers(store, source_dir, overwrite=False) -> {"imported","skipped","failed"}`. |
| `app/warmup.py` | `WarmupService(store, engine, settings)`; attributes `warm: bool`, `last_error: str|None`; `run()->bool`, `run_forever()`. |
| `app/main.py` | `create_app(settings=None, *, engine=None)`; lifespan builds `app.state.{settings,store,takes,engine,speech,warmup}`. Pass `engine=` in tests. Static at `/static`, `/` serves `static/index.html`. Basic-auth middleware for `/`, `/api/*`, `/static/*` when `STUDIO_AUTH` set. Validation errors → 400 OpenAI shape. |
| `app/auth.py` | `require_v1` dependency (Bearer when `API_KEYS` set). `/v1` router must include it. |
| `app/routers/system.py` | `/health` 200 always; `/ready` 200 only when `warmup.warm and engine.ready`, else 503 + `Retry-After: 5` with a reason. |

## 2. HTTP routes (exact paths)

`/v1` (add `dependencies=[Depends(require_v1)]` on the router):

- `POST /v1/audio/speech` → audio bytes, `Content-Type` per format, headers `X-Voice`,
  `X-Sample-Rate`, `X-Audio-Duration` (non-stream), `X-RTF` (non-stream).
- `GET /v1/models` → **one** entry `{"object":"list","data":[{"id":"voxcpm2","object":"model",
  "created":0,"owned_by":"openbmb"}]}`. Aliases `tts-1`/`openbmb/VoxCPM2` are accepted as the
  request `model`, not listed (SPEC §6).
- `GET /v1/audio/voices` → `{"voices":[{"name","language","source"}]}` where `name` is the
  lowercase **key** callers pass as `voice` (SPEC §6).

`/api`:

| Method | Path | Response |
|---|---|---|
| GET | `/api/voices` | `{"voices":[VoiceSummary]}` |
| GET | `/api/voices/{name}/audio` | WAV bytes |
| PATCH | `/api/voices/{name}` | `VoiceSummary` (body `VoicePatch`) |
| DELETE | `/api/voices/{name}` | `{"deleted": name}` |
| POST | `/api/voices/clone` | `VoiceSummary` (multipart, see §6) |
| POST | `/api/voices/import` | `{"imported":[],"skipped":[],"failed":[]}` |
| POST | `/api/transcribe` | `{"text": str}` (multipart `file`) |
| POST | `/api/speak` | `TakeInfo`; `stream=true` → audio + `X-Take-Id` |
| GET | `/api/takes?kind=studio` | `{"takes":[TakeInfo]}`; default `studio`, `all` includes design candidates |
| GET | `/api/takes/{id}/audio` | WAV bytes |
| GET | `/api/takes/{id}/wave` | `{"amps":[72 floats 0..1]}` |
| POST | `/api/takes/{id}/save-as-voice` | `VoiceSummary` (body `{name,language}`) |
| DELETE | `/api/takes/{id}` | `{"deleted": id}` |
| POST | `/api/design/candidates` | `{"candidates":[CandidateInfo]}` |
| POST | `/api/design/save` | `VoiceSummary` |
| GET | `/api/engine` | `EngineStatus` |
| POST | `/api/engine/warmup` | `{"warm": true}` |
| POST | `/api/engine/restart` | `{"restarted": true, "warm": true}` |

`VoiceSummary` includes `key` (the lowercase lookup key) **and** `name` (display). UI clients must
use `key` in `/api/voices/{key}` URLs and as the `voice` field; `name` is for display only
(SPEC §5 `voice_name_lower` vs `name`).

`TakeInfo` JSON: `id, voice, text, language, kind, duration_s, rtf, first_audio_ms, created_at,
audio_url, wave_url`. URL paths are relative (`/api/takes/<id>/audio`).

`VoiceSummary`: `key, name, language, source, ref_duration_s, ref_text, created_at`.

`EngineStatus`: `ready, model, reason (warm-up error when not ready), runtime, gpu,
vram_used_mb, vram_total_mb, kv_cache, max_concurrency, rtf, first_audio_ms, voices,
queue_depth, stt (STT_URL configured), api_keys_required (API_KEYS set)`. The last two let the UI
disable auto-transcribe / Test-a-line exactly as SPEC §8/§10 require.

Errors on both prefixes use `{"error":{"message","type","param","code"}}`.

## 3. `/v1/audio/speech` semantics

- `model` accepted as any string. Unknown extra fields ignored (`task_type` included).
- `input` 1..4000 chars (enforced by `SpeechRequest` and again by the routers using
  `settings.max_input_chars`) → else 400. `voice` case-insensitive; unknown → 400 listing names.
- `ref_audio` present (data URI, or http(s) only when `ALLOW_REMOTE_REF_AUDIO=1`) → ad-hoc clone;
  `voice` is then only a label and need not exist.
- `response_format` default `wav`; `pcm` = s16le mono at `sample_rate` (default 48000).
- `stream=true`: engine PCM stream; `pcm` passthrough at 48 kHz, `wav` gets a 44-byte header,
  `mp3`/`opus`/`flac` go through an ffmpeg pipe. `speed` is ignored for streams (log once).
- Queue: > `max_queue` waiting → `QueueFull` (429, `Retry-After: 1`).

## 4. Voice/take store

- Voice safetensors metadata keys: `name, voice_name_lower, ref_text, sample_rate, created_at,
  consent, embedding_source=audio, language, source, mime_type, original_filename, file_size`,
  and for designed voices `design_prompt`, `design_seed`. All values strings.
- `VoiceStore`: `get`, `list`, `exists`, `read_audio`, `data_uri`, `create(name, samples,
  sample_rate, *, ref_text, language, consent, source="uploaded", metadata=None, overwrite=False,
  key=None)` (key defaults to `validate_voice_name(name)`),
  `update(name, *, new_name=None, ref_text=None, language=None)`, `delete`, `backup(reason)`.
  Name pattern `^[a-z0-9][a-z0-9_-]{0,63}$` (validated on the lowercase key).
- `TakeStore`: `new_id()`, `create(samples, sample_rate, *, voice, text, language="", kind="studio",
  rtf=None, first_audio_ms=None, extra=None, take_id=None)`, `get`, `list(limit=None)` newest-first,
  `audio_path`, `wave` (72 amps), `delete`, `prune`, `take_urls`.
- Takes are WAV on disk, 50 kept by default. Design candidates are takes with `kind="design"`.

## 5. Engine client

```python
EngineClient(base_url, *, model, concurrency, max_queue, metrics_window, timeout,
             s6_service, service_dir, kv_cache)   # + from_settings(settings)
await start(); await aclose()
ready -> bool                                     # last /health
await health() -> bool
await wait_ready(max_wait) -> bool
await synth(SynthParams) -> SynthResult           # fmt="wav", WAV bytes
await open_stream(SynthParams) -> StreamHandle    # admission first; handle.chunks, handle.aclose()
await restart()                                   # s6-svc -r <service_dir>/<s6_service>
stats() -> EngineStats
```

`StreamHandle.chunks` yields s16le mono 48 kHz; `aclose()` releases the engine slot and
closes the upstream response. `SpeechService.stream()` must await `open_stream` before returning so
`QueueFull`/`EngineNotReady` reach the router. `StreamingSpeech` also exposes
`await aclose()`; routers **must** `try/finally: await streaming.aclose()` around the response so a
client that disconnects before the first chunk cannot leak the slot. `Stats` must be read off the
event loop (`asyncio.to_thread`) because it shells out to `nvidia-smi`.

- POST JSON to `{base}/v1/audio/speech` with `model`, `input`, `ref_audio`, `ref_text`;
  optional `instructions, speed, seed, cfg, steps`; `response_format` `wav|pcm`; `stream`.
- Engine output is assumed s16le mono 48 kHz for `stream=true` (SPEC §6; verify in S3).
- `cfg`/`steps` rejected with 4xx → retry once without them, log once (Q-004).
- Record RTF (= wall time / audio duration; SPEC §2 reports ~0.14) and first-chunk ms in a rolling deque of
  `metrics_window`; expose averages. Increment queue depth while waiting; raise `QueueFull`
  when waiting ≥ `max_queue`.
- VRAM/GPU from `nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader,nounits
  -i 0`; failures → `None` (never raise from `stats()`).
- `restart()` marks the client unready, runs `s6-svc -r /run/service/<s6_service>`, waits up to
  120 s for `/health`, and raises `EngineError` if it never returns.

## 6. Clone / transcribe

- Multipart fields: `file`, `name`, `language`, `ref_text`, `consent` (`"true"` required).
- Upload: size ≤ 20 MB, decoded with `soundfile` (not extension), duration 3–30 s.
- Normalize: mono, trim silence, peak −1 dBFS; keep original rate, resample down to 48 kHz if
  above. Store consent `uploaded-via-studio`.
- `STT_URL` (OpenAI base ending in `/v1`): POST `{STT_URL}/audio/transcriptions` multipart
  `file`; return its `text`. Empty `STT_URL` → 503 "transcription is not configured".

## 7. UI module contract (parallel-safe)

`app/static/index.html` loads modules in this order:
`core.js`, `speak.js`, `voices.js`, `clone.js`, `design.js`, `engine.js` (all `type="module"`).

`core.js` exports:

```js
export const api = { get, post, patch, del, postForm };   // JSON; throws Error(message)
export const state = { voices: [], selectedVoice: null, engine: null };
export function loadVoices();                             // fills state.voices
export function refreshEngine();                          // fills state.engine + header text
export function registerScreen(name, { init, show });     // names: speak|voices|clone|design|engine
export function selectVoice(name);
export function fmtDuration(s), fmtClock(unix), fmtBytes(n);
export function wavePath(amps), renderWave(svg, amps, current=false);
export function play(url), stopAudio();
export function showError(screen, message), clearError(screen);
export function formatLang(code);                          // en→English, ja→Japanese, ko→Korean, zh→Chinese
```

- `registerScreen` runs `init()` once on DOMContentLoaded and `show()` on `hashchange` when active.
- Every screen has `<p class="vs-error" id="<screen>-error" role="alert">`; use
  `showError(screen, message)`.
- Use `textContent` only for user data; never `innerHTML` with API/spec text.
- Element IDs the screen modules own (defined by M3-T1 in `index.html`):

| Screen | IDs |
|---|---|
| header | `engine-status`, `.vs-tab[data-tab]` |
| speak | `voice-chips`, `speak-text`, `speak-style`, `cfg`, `cfg-out`, `steps`, `steps-out`, `fmt`, `stream-toggle`, `generate`, `takes-list`, `speak-error` |
| voices | `voice-search`, `lang-filters`, `voice-rows`, `voices-count`, `voices-error` |
| clone | `drop-zone`, `ref-file`, `ref-text`, `auto-transcribe`, `clone-name`, `clone-lang`, `consent`, `test-line`, `save-voice`, `clone-error` |
| design | `design-desc`, `design-tags`, `design-lang`, `design-n`, `design-sample`, `generate-candidates`, `candidates`, `design-name`, `save-designed`, `design-error` |
| engine | `engine-stats`, `run-warmup`, `restart-engine`, `engine-endpoint`, `engine-error` |

- Waveforms: `renderWave(svg, amps)` sets `d = wavePath(amps)` on the existing `<path>`; the newest
  take / selected candidate gets `class="vs-wave is-current"`.

## 8. Test fixtures (`tests/conftest.py`)

- `fake_engine` (session): `FakeEngine` with `.url`, `.state`, `.reset()`; serves `/health`,
  `/v1/audio/speech` (WAV 0.6 s; stream = 10 PCM chunks), `/debug/state`.
- `settings(tmp_path, fake_engine)`: isolated data dir; `library` creates `ashley-en`,
  `ashley-ja`; `client` = `TestClient(create_app(settings))` with lifespan.
- `make_voice(name, language, seconds)` writes a voice through the real store.
- Use `client` for HTTP tests, `store`/`library` for disk tests. Do not edit `conftest.py`
  (owned by the lead); request fixture changes via a new task.

## 9. Commands

- Tests: `.venv/bin/python -m pytest <path> -q`
- Lint/format: `.venv/bin/ruff check <files>` · `.venv/bin/ruff format <files>`
- Types: `.venv/bin/mypy app`
- Full gate: `scripts/verify.sh`

## 10. Decisions that workers must not re-litigate

- `Q-006` streaming `/api/speak` returns audio with `X-Take-Id`; the take is written when the
  stream completes.
- `Q-007` design candidates are takes with `kind="design"`, `extra={"design_prompt": ...,
  "design_seed": ...}`.
- `Q-009` "Test a line" uses `/v1/audio/speech` with `ref_audio`; no dedicated route.
- `Q-016` capacity is 2 active + 16 queued; requests 19–20 get 429.
- `Q-020` remote `ref_audio` URLs are rejected unless `ALLOW_REMOTE_REF_AUDIO=1`.
