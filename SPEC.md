# voxcpm2-openai — VoxCPM2 TTS service with Voice Studio

Project spec for implementation. Read this whole file before starting. Section 12 lists the facts that must be verified first; do those spikes before building on them.

## 1. Goal

Replace the Qwen3-TTS service (`qwen3-tts-openai`) on the `vllm` host with a single container that:

1. Serves **VoxCPM2** through **vLLM-Omni** on the RTX 3090 speech card.
2. Exposes an **OpenAI-compatible** `/v1/audio/speech` endpoint so existing callers (LiteLLM `tts` alias, Open WebUI, Hermes, the Wyoming bridge) keep working by voice name with no client changes.
3. Ships a **Voice Studio** web UI for synthesizing, managing voices, cloning from recordings, and designing voices from text descriptions.

VoxCPM2 replaces both Qwen3 engines (Base for cloning, VoiceDesign for designed voices) with one model.

## 2. Context and measured facts

Validated by hand on the `vllm` host. Treat these as known-good.

| Item | Value |
|---|---|
| Host | `vllm`, Podman, CDI GPU devices |
| GPU | RTX 3090, UUID `GPU-79ad6f3b-cc71-90a6-2430-286e1c6c9ec2` (index 1), shared with `whisper-stt` and `tei-embed` / `tei-rerank` |
| Weights | `/models/openbmb/VoxCPM2` (4.7 GB, local, offline) |
| Runtime | `vllm 0.30.0` + `vllm-omni 0.30.0` (from `harbor.dotslash.io/dotslash/qwen3-tts-openai:1.4.3`) + `voxcpm==2.0.3` |
| Deploy config | `deploy/voxcpm2-3090.yaml` (single stage, 2 GiB KV, max 2 seqs) |
| VRAM | ~9.3 GB for the engine |
| Speed (warm) | RTF ~0.14 non-streamed; ~154 ms to first streamed audio |
| Cold start | first request after start took ~14.5 s; must be hidden by a warm-up (section 4) |
| Output | 48 kHz mono |
| Languages tested | English, Japanese, Korean, Chinese (cloned voices), all judged good |
| Cloning request | inline `ref_audio` (`data:audio/wav;base64,...`) + `ref_text` on `/v1/audio/speech` works |

Install gotchas already solved:

- vLLM-Omni's VoxCPM2 model imports the upstream `voxcpm` package. Without it: `No module named 'voxcpm'`.
- Install `voxcpm` with **uv**, not pip. The image's `/etc/uv-overrides.txt` pins `nvidia-nccl-cu13==2.30.7`; pip would downgrade it to 2.29.7.
- `trust_remote_code` needs a writable HF cache. `/models` is mounted read-only, so set `HF_HOME` to a writable path.
- The stock `vllm_omni/deploy/voxcpm2.yaml` asks for `gpu_memory_utilization: 0.9` and a 6 GiB KV cache (~13 GiB peak) and will not start on the shared card. Use `deploy/voxcpm2-3090.yaml`.
- `vllm serve` logs a harmless `config.json ... model_type` error; vLLM-Omni patches the config on the next line.

## 3. Architecture

One container, two supervised processes (same pattern as `qwen3-tts-openai`, which uses s6-overlay):

```
                 :8000 (published as 8880)
 clients ──► ┌──────────────────────────────────────────┐
             │ app (FastAPI, uvicorn)                    │
             │  /v1/*   OpenAI-compatible TTS            │
             │  /api/*  Studio API                        │
             │  /       Studio UI (static)               │
             │  voice store, takes, warm-up, metrics      │
             └───────────────┬──────────────────────────┘
                             │ http://127.0.0.1:8102/v1/audio/speech
             ┌───────────────▼──────────────────────────┐
             │ engine: vllm serve /models/openbmb/VoxCPM2 │
             │   --omni --deploy-config voxcpm2-3090.yaml │
             └──────────────────────────────────────────┘
 /data (rw): voices, takes, backups, settings    /models (ro): weights
```

- The engine binds to `127.0.0.1` only; nothing outside the container talks to it.
- The app is the only public surface. It resolves voice names to reference audio and transcript, forwards to the engine, re-encodes audio, and streams responses through.
- Base image and build notes: `deploy/Containerfile`. Compose: `deploy/compose.yaml`.
- Suggested stack: Python 3.12, FastAPI, httpx (streaming proxy), soundfile + numpy, ffmpeg for mp3/opus/flac, safetensors. Frontend: plain HTML/CSS/JS or a small framework, matching `ui/` exactly. No CDN dependencies at runtime except Google Fonts (or self-host the two fonts).

## 4. Engine management

- s6 service `engine` runs `vllm serve $MODEL_PATH --omni --deploy-config $DEPLOY_CONFIG --served-model-name openbmb/VoxCPM2 --host 127.0.0.1 --port $ENGINE_PORT`.
- Readiness: poll `http://127.0.0.1:$ENGINE_PORT/health`. When healthy, the app runs a **warm-up**: one short synthesis with inline reference audio (use the first library voice, or bundled audio if the library is empty), plus one streamed request. Only after warm-up does `/ready` return 200.
- `/api/engine/warmup` re-runs the warm-up on demand. `/api/engine/restart` restarts the s6 engine service and re-runs warm-up.
- Concurrency: the engine accepts 2 sequences. The app runs at most 2 engine requests at once with an asyncio semaphore and queues the rest (bounded queue of 16, then HTTP 429 with `Retry-After`).
- Engine metrics for the Engine screen: VRAM via `nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits -i 0` inside the container (the card is device 0 there); RTF and first-audio latency as rolling averages of the last 20 requests, measured by the app. Exclude warm-up requests and any request that started before `/ready` went green; otherwise the cold first request (~14.5 s) skews RTF to ~2.3.

## 5. Data model and storage

Everything under `DATA_DIR=/data`:

```
/data/
  voices/<name>.safetensors     one file per voice (format below)
  voices.json                   index: ordering, display metadata, cached durations
  takes/<id>.wav + <id>.json    Studio takes (48 kHz WAV + metadata)
  backups/                      timestamped zips of voices/ + voices.json before destructive changes
  settings.json                 defaults (cfg, steps, format, stream, take retention)
```

### Voice file format

Same layout as the Qwen3 shim's speaker files (no import from Qwen3; voices are created fresh in the Studio):

- Tensor `audio`: float32, mono, at `metadata.sample_rate`.
- String metadata (all values are strings, a safetensors constraint):

| key | example | notes |
|---|---|---|
| `name` | `ashley-en` | display name |
| `voice_name_lower` | `ashley-en` | lookup key; unique; `[a-z0-9][a-z0-9_-]{0,63}` |
| `ref_text` | `Thanks for waiting. ...` | exact transcript of `audio` |
| `sample_rate` | `24000` | of the stored tensor |
| `created_at` | `1791831655` | unix seconds |
| `consent` | `uploaded-via-studio` / `own-generated-voice` | required; see section 10 |
| `embedding_source` | `audio` | keep `audio`; vLLM-Omni rejects `direct` (Qwen3-only) for VoxCPM2 |
| `mime_type`, `original_filename`, `file_size` | | from the uploaded clip (clone only) |
| `language` | `en` / `ja` / `ko` / `zh` | chosen in Clone / Design |
| `source` | `uploaded` / `designed` | |
| `design_prompt`, `design_seed` | | **new**, designed voices only |

Reading a voice (reference code):

```python
from safetensors import safe_open
f = safe_open(path, "pt")
meta = f.metadata()
audio = f.get_tensor("audio").numpy()      # float32 mono
sr = int(meta["sample_rate"]); ref_text = meta["ref_text"]
```


## 6. OpenAI-compatible API (`/v1`)

### `POST /v1/audio/speech`

Request (JSON):

| field | type | required | behavior |
|---|---|---|---|
| `model` | string | yes | accept any value (`tts-1`, `voxcpm2`, `openbmb/VoxCPM2`, ...); existing callers send `tts-1` |
| `input` | string | yes | text to speak, max 4000 chars; longer → 400 |
| `voice` | string | yes | library voice name, case-insensitive. Unknown → 400 listing valid names |
| `response_format` | `wav` \| `pcm` \| `mp3` \| `opus` \| `flac` | no (default `wav`) | `pcm` = raw s16le mono at `sample_rate` |
| `stream` | bool | no (default false) | stream chunks as they are generated (`pcm` recommended; `wav` streams with a header; mp3/opus stream via ffmpeg pipe) |
| `instructions` | string | no | style control ("calm, level, brisk"); see section 12, spike S1 |
| `speed` | number 0.5–2.0 | no | if the engine has no native control, apply ffmpeg `atempo` for non-streamed responses and ignore for streams (log once) |
| `sample_rate` | int | no (extension) | resample output (e.g. 22050 / 24000 for Wyoming); default 48000 |
| `ref_audio`, `ref_text` | string | no (extension) | ad-hoc clone without saving a voice; `ref_audio` is a data URI or URL |
| `cfg`, `steps`, `seed` | number | no (extension) | pass through to the engine if supported (spike S4); otherwise ignore |

Voice resolution: load the voice's `audio` + `ref_text`, encode the audio as a WAV data URI, send `ref_audio` + `ref_text` to the engine. Cache encoded data URIs in memory keyed by voice name and file mtime.

Response: audio bytes with the matching `Content-Type` (`audio/wav`, `audio/pcm`, `audio/mpeg`, `audio/ogg`, `audio/flac`). Add headers `X-Voice`, `X-Sample-Rate`, `X-Audio-Duration` (non-streamed), `X-RTF` (non-streamed).

Errors: OpenAI error shape `{"error": {"message", "type", "param", "code"}}`. 400 bad input, 401 bad key, 429 queue full, 503 engine not ready (with `Retry-After`).

### Other `/v1` routes

- `GET /v1/models` → one model entry, id `voxcpm2` (plus aliases `tts-1`, `openbmb/VoxCPM2`).
- `GET /v1/audio/voices` → `{"voices": [{"name", "language", "source"}]}`.

### Compatibility with the Qwen3 shim

Before cutover, read the Qwen3 shim's `/opt/app/app/openai_api.py` (inside the `qwen3-tts-openai` container) and accept any request fields its callers actually send. Known: it mapped `preset` / `clone` / `design` task types to engines. Accept and ignore a `task_type` field. Every existing voice name must keep working.

## 7. Studio API (`/api`)

All JSON unless noted. Protected by `STUDIO_AUTH` when set (section 10).

| method | path | purpose |
|---|---|---|
| GET | `/api/voices` | list voices with name, language, source, ref duration, ref_text, created_at |
| GET | `/api/voices/{name}/audio` | reference clip as WAV (for Audition) |
| PATCH | `/api/voices/{name}` | rename, edit `ref_text`, edit `language` (backup first) |
| DELETE | `/api/voices/{name}` | delete (backup first) |
| POST | `/api/voices/clone` | multipart: `file` (wav/flac/mp3, 3–30 s, ≤20 MB), `name`, `language`, `ref_text`, `consent=true` (required). Normalize: mono, trim leading/trailing silence, peak-normalize to -1 dBFS, store float32 at the original rate (resample to 48 kHz if above) |
| POST | `/api/transcribe` | multipart `file` → `{"text"}` via `STT_URL` (OpenAI `/audio/transcriptions` shape); used by "Auto-transcribe" |
| POST | `/api/speak` | Studio synthesis: same fields as `/v1/audio/speech`, saves a take, returns take metadata and an audio URL. If `stream=true`, return the stream and save the take when it ends |
| GET | `/api/takes` | takes for the session list (newest first; keep last 50 by default) |
| GET | `/api/takes/{id}/audio` | take audio (WAV) |
| GET | `/api/takes/{id}/wave` | `{"amps": [72 floats 0..1]}` RMS envelope for the waveform |
| POST | `/api/takes/{id}/save-as-voice` | `{name, language}` → creates a voice from the take audio and its text |
| DELETE | `/api/takes/{id}` | delete a take |
| POST | `/api/design/candidates` | `{description, sample_text, language, n (1–4)}` → n candidates with seed, audio URL and wave; see spike S2 |
| POST | `/api/design/save` | `{candidate_id, name, language}` → voice with `source=designed`, `ref_text=sample_text`, `design_prompt`, `design_seed` |
| GET | `/api/engine` | status: ready, model, runtime versions, GPU name, VRAM used/total, KV cache, max concurrency, rolling RTF and first-audio latency, voice count, queue depth |
| POST | `/api/engine/warmup` | re-run warm-up |
| POST | `/api/engine/restart` | restart engine service, then warm-up |
| GET | `/ready`, `/health` | readiness (engine warm), liveness |

## 8. UI spec

The visual reference is `ui/studio.html` with `ui/tokens.css`. Open it in a browser: all five screens work as a static prototype (hash routes `#speak`, `#voices`, `#clone`, `#design`, `#engine`). Match it closely. The original design canvas (claude.ai artifact "VoxCPM2 Voice Studio") is the source these files were derived from.

### Global

- Header: red square mark, "Voice Studio" title, meta block "Model / Engine". The Engine value is live from `/api/engine`: `Ready · RTX 3090`, `Warming up…`, or `Not ready · <reason>` (reason in text, never color alone).
- Tabs: 01 Speak, 02 Voices, 03 Clone, 04 Design, 05 Engine. Hash-routed; the active tab has a 2 px accent underline directly under the label.
- Layout: max width 1280 px, 32 px side padding. Two-column screens use a main column (`flex: 999 1 560px`) and side column (`flex: 1 1 380px`), stacking on narrow screens.
- Form rows: 112 px label column on the left, control on the right, 1 px rule between rows, 2 px rule above the action row.

### 01 Speak

- Voice chips from `/api/voices`; single select; remembers the last voice (localStorage).
- **Empty library guard:** with no voices, replace the chips with a notice ("No voices in the library yet", one line of explanation, Clone from audio / Design a voice buttons), show "Add a voice to enable Generate" beside the Generate button, set `aria-disabled="true"` on it (clicking moves focus to the notice's first button instead of submitting), and show "No takes yet" in the Takes column. Text, presets and parameters stay editable. Server side, `/api/speak` and `/v1/audio/speech` with an empty library return 400 `{"error": {"code": "no_voices", "message": "No voices exist yet. Create one in the Studio (Clone or Design) first."}}`. Reference: `ui/studio.html?empty`.
- Text (13 px, same size as style control), style control (optional), parameters: CFG, diffusion steps, format. Hide CFG / steps if spike S4 shows they can't be passed through.
- **Test phrases** row under Text: six preset chips (Greeting, Status update, Numbers & dates, Question, Long passage, Tongue twister) from `ui/presets.json`, in the **selected voice's language** (from the voice's `language`). Clicking a chip replaces the Text box content and marks the chip pressed. Switching to a voice in another language keeps the same preset and swaps in that language's version. Default selection: Question. Serve the presets from the app (static file or `GET /api/presets`).
- Stream toggle and Generate button. Ctrl/Cmd+Enter in the text box generates.
- While generating: the Generate button reads "Generating…" and is disabled; with streaming, playback starts when the first chunk arrives (Web Audio, s16le PCM at 48 kHz).
- Takes column: newest first. Each take shows voice, stats (duration · RTF · first-audio ms), time, text clamped to 2 lines, play button, smooth waveform, Download and Save as voice. The newest take's waveform uses the accent; older ones are grey.
- Errors appear inline above the action row in plain text ("Engine is warming up. Try again in a few seconds.").

### 02 Voices

- Table: Name, Language, Source (outlined square = uploaded, filled square = designed, plus the word), Reference length, Transcript (one line, ellipsis), actions Audition / Edit / Delete.
- Search (name and transcript) and language filters (All, English, Japanese, Korean, Chinese).
- Edit opens an inline row editor (name, language, transcript). Delete asks to confirm in place ("Delete ashley-en? This can't be undone from the UI." with Delete / Cancel); a backup is still written.
- Header buttons (Clone from audio, Design a voice) go to Clone and Design, and appear **only when the library has voices**.
- Empty library: hide the header buttons, the search/filter toolbar and the table, and show one empty state instead: "No voices yet", one line of explanation, the same two buttons with the same labels (Clone from audio, Design a voice). Never show both sets of buttons. Reference: `ui/studio.html?empty`.

### 03 Clone

- Drop zone (drag-over state: solid accent border), file picker, client-side duration check (3–30 s) with inline error.
- Transcript with "Auto-transcribe with whisper-stt" (calls `/api/transcribe`; fills the box; disabled when `STT_URL` is unset).
- Name (validated live against the name pattern and existing names), language, consent checkbox (required to enable Save voice).
- "Test a line" synthesizes a fixed sample sentence in the chosen language with ad-hoc `ref_audio` + `ref_text` without saving.
- Side panel: the three short notes from the reference.

### 04 Design

- Description (15 px), descriptor tags that append to the description, language, number of candidates, sample line (becomes the saved transcript).
- The sample line has the same six preset chips under it, in the **Design language** selected above; changing the language re-renders the chips (and swaps the text if a preset is selected).
- Generate candidates → list with play, waveform, seed, Select. One selection at a time; the selected candidate gets a 3 px accent top rule and accent waveform.
- Name field and Save as voice (disabled until a candidate is selected and the name is valid).

### 05 Engine

- Stat grid from `/api/engine`, refreshed every 5 s while visible, fixed at 4 columns (2 below 900 px, 1 below 520 px). Each tile has a short one-line value and the detail in the note: Model `VoxCPM2` (note `openbmb/VoxCPM2 · 2B · 48 kHz`), GPU `RTX 3090` (strip the `NVIDIA GeForce ` prefix; full name in `title`), GPU memory used (whole card, note "of 24 GB, all processes on the card"), KV cache, Warm RTF, First audio, Voices, Runtime `vLLM-Omni` (note `0.30.0 · vLLM 0.30.0`). Values never wrap: `white-space: nowrap` with ellipsis.
- Run warm-up, Restart engine (confirm in place).
- Endpoint example: the real base URL plus a sample request.

### Waveforms

Render as one smooth mirrored SVG path in a `300×24` viewBox with `preserveAspectRatio="none"`. Use `wavePath(amps)` from `ui/studio.html` with the real 72-bucket RMS envelope from `/api/takes/{id}/wave`. Not vertical bars.

### Accessibility floor

Real buttons and labels everywhere, `aria-pressed` on toggles and chips, `aria-current="page"` on the active tab, visible focus ring (2 px accent outline), text contrast ≥ 4.5:1 (tokens already pass), reduced motion respected.

## 9. Design tokens

`ui/tokens.css` is the source of truth. Summary: dark ground `#0E0E0E`, text `#EDEDED`, muted `#9A9A9A`, one accent `#FF3B30` with `#0E0E0E` text on it; Inter Tight (400/500/700) for everything, IBM Plex Mono only for code; 0 border radius; lines instead of cards; no shadows or gradients. Control heights: primary 34, input 32, chip 28, link 24 px.

## 10. Security

- `STUDIO_AUTH="user:password"`: when set, HTTP basic auth on `/`, `/api/*`. `/v1/*`, `/ready` and `/health` stay open on the LAN unless `API_KEYS` is set.
- `API_KEYS="k1,k2"`: when set, `/v1/*` requires `Authorization: Bearer <key>`. LiteLLM passes its configured key.
- Clone uploads require `consent=true`; store `consent` in the voice file. Designed voices use `own-generated-voice`.
- Upload limits: 20 MB, audio types only (check with soundfile, not the extension), 3–30 s.
- Never execute or render user-supplied text as HTML (transcripts, names).

## 11. Deployment and cutover

1. Build `harbor.dotslash.io/dotslash/voxcpm2-openai:0.1.0` from `deploy/Containerfile`; push to Harbor.
2. Run with `deploy/compose.yaml` on port **8881**, next to the existing `qwen3-tts-openai` on 8880. VRAM: GPU 1 has room only while the Qwen3 VoiceDesign engine is disabled (it is, as of this spec).
3. Verify acceptance tests (section 13) against 8881.
4. Point the LiteLLM `tts` alias at `http://vllm.dotslash.io:8881/v1`, test Open WebUI, Hermes and the Wyoming bridge.
5. Cutover: stop `qwen3-tts-openai`, move the new service to host port 8880, point LiteLLM back to 8880.
6. Rollback: stop the new service, start `qwen3-tts-openai`, restore the LiteLLM target. Keep `/opt/qwen3-tts` untouched until the new service has run cleanly for a week.

## 12. Verify first (spikes)

Each spike is a short script against a running engine (the test container `voxcpm2-test` on `127.0.0.1:8881` already serves VoxCPM2 with the validated config). Record findings in `NOTES.md` and adjust the design if a spike fails.

- **S1 Style control.** Does vLLM-Omni's VoxCPM2 adapter accept a style or instruction field (`instructions`, or VoxCPM2's controllable-cloning syntax in the text)? Read `vllm_omni/entrypoints/openai/tts_adapters/voxcpm2.py` and `vllm_omni/model_executor/models/voxcpm2/voxcpm2_talker.py` (`build_voxcpm2_prompt`). If it is unsupported, hide the Style control field and drop `instructions` (accept it, ignore it, log once).
- **S2 Voice design.** How does vLLM-Omni express VoxCPM2 voice design (a description with no reference audio)? Check the adapter and the upstream `voxcpm` package's voice design usage. Confirm that different seeds give different speakers, and how a seed is passed per request. If it can't go through vLLM-Omni, generate design candidates with the `voxcpm` Python API in a short-lived worker, and document the extra VRAM it needs.
- **S3 Streaming format.** With `stream=true, response_format=pcm`, determine sample rate, sample type (s16le or float32) and chunking. In testing, a streamed English line decoded as 7.69 s of 16-bit 48 kHz audio while the WAV version of the same line was 5.76 s: find out whether streaming adds untrimmed trailing silence or uses a different format, and normalize the app's output to s16le at the requested `sample_rate`.
- **S4 Sampling knobs.** Can CFG, diffusion steps and seed be set per request (sampling params or extra fields)? If not, remove those controls from the UI.
- **S5 Warm-up.** Confirm that one non-streamed plus one streamed request after start removes the ~14.5 s first-request delay.
- **S6 Qwen3 shim field compatibility** (section 6).

## 13. Acceptance criteria

- `POST /v1/audio/speech` with `{"model":"tts-1","voice":"ashley-en","input":"...","response_format":"wav"}` returns 48 kHz WAV; warm RTF ≤ 0.2 on the shared 3090.
- Streamed PCM first chunk ≤ 300 ms after warm-up.
- Voices re-created in the Studio (e.g. ashley-en/ja/kr/zh) synthesize correctly in their language; spot-check by ear against the Qwen3 versions.
- First request after container start takes no longer than a warm request (warm-up done before `/ready`).
- Clone flow: upload → auto-transcribe → save → voice appears in Speak and in `/v1/audio/voices`, usable by name over `/v1`.
- Design flow: description → 3 candidates → select → save → voice works by name.
- LiteLLM `tts`, Open WebUI, Hermes and the Wyoming bridge work unchanged.
- Engine VRAM stays ≤ 10 GB; whisper-stt and TEI keep running on the same card.
- Two concurrent requests both succeed; a burst of 20 queues without errors or returns 429 cleanly.
- UI matches `ui/studio.html` (layout, tokens, waveforms) and passes keyboard-only navigation.

## 14. Milestones

1. **M0 Spikes:** S1–S6, findings in `NOTES.md`.
2. **M1 Engine + `/v1`:** container, s6 services, warm-up, voice resolution, formats, streaming, queueing. Acceptance criteria for `/v1` pass.
3. **M2 Voice store + Studio API:** voices CRUD, clone, transcribe, takes, engine status.
4. **M3 UI:** all five screens wired to the API.
5. **M4 Design flow:** candidates and save (depends on S2).
6. **M5 Cutover:** section 11.

## 15. Files in this bundle

```
SPEC.md                      this file
ui/tokens.css                design tokens
ui/studio.html               static, clickable reference of all five screens + wavePath() (add ?empty for the empty library)
ui/presets.json              test phrases for Speak and Design, en/ja/ko/zh
ui/icons/                    app icon (red square, five bars, off-white peak): favicon.svg, favicon.ico (16/32/48),
                             favicon-16.png, favicon-32.png, apple-touch-icon.png (180), icon-192.png, icon-512.png;
                             concepts/ holds the other explored directions (reference only)
ui/starting.html             engine startup page (see ui/STARTING.md)
ui/STARTING.md               backend contract for the startup page
deploy/voxcpm2-3090.yaml     validated vLLM-Omni deploy config
deploy/Containerfile         reference build (base image, voxcpm via uv, env)
deploy/compose.yaml          reference compose (CDI GPU, volumes, ports)
```
