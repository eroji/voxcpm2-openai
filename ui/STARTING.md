# Engine startup page — implementation notes

Asset: `ui/starting.html` (uses `ui/tokens.css`). Open it from disk to see a demo; add `?demo=failed` for the failure state.

## Serving rule

Until `GET /ready` returns 200, the app serves `starting.html` for `/` and every Studio route (`/`, `/#speak`, and so on). The `/api/*` Studio endpoints, except the two below, return 503. The page polls every 2 s and calls `location.replace('/')` once `/ready` is 200.

`/v1/*` keeps returning `503` with `Retry-After: 5` while starting (unchanged from SPEC section 6).

## `GET /api/engine/startup`

Always available, no engine needed, covered by `STUDIO_AUTH` like the rest of `/api`.

```json
{
  "state": "starting",
  "elapsed_s": 72,
  "steps": [
    { "label": "Container started",     "detail": "App online, voice library loaded (8 voices)", "status": "done",    "duration_s": 2 },
    { "label": "Loading model weights", "detail": "openbmb/VoxCPM2 · 4.7 GB from /models",       "status": "done",    "duration_s": 21 },
    { "label": "Starting the engine",   "detail": "vLLM-Omni 0.30.0 · 2 GiB KV cache · RTX 3090", "status": "active",  "duration_s": null },
    { "label": "Warming up",            "detail": "One full and one streamed synthesis, so the first real request is fast", "status": "waiting", "duration_s": null }
  ],
  "error": null,
  "restart_in_s": null,
  "log": ["... last 16 lines, oldest first ..."]
}
```

- `state`: `starting` | `ready` | `failed`.
- `status` per step: `done` | `active` | `waiting` | `failed`. At most one step is `active` or `failed`.
- `elapsed_s`: seconds since the container (app) started.
- `error`: one plain-language sentence for the failed step (what happened and the likely cause), not a stack trace. Map known failures, for example:
  - `CUDA out of memory` → "CUDA out of memory while allocating the KV cache. Another process may be using GPU memory."
  - `No module named 'voxcpm'` → "The voxcpm package is missing from the image. Rebuild with voxcpm installed via uv."
  - process exit with no match → "The engine process exited with code N. See the engine log."
- `restart_in_s`: countdown to the automatic engine restart after a failure (s6 restarts the service; back off 30 s, 60 s, 120 s, then stay failed until a manual restart).
- `log`: tail of the merged engine and app log, ANSI codes stripped, each line trimmed to 200 characters, oldest first. Keep a 200-line ring buffer and return the last 16.

## Deriving the phase

The app tracks the four steps itself:

1. **Container started:** done as soon as the app has loaded the voice index.
2. **Loading model weights:** active once the engine process is spawned. Done when the engine log contains `Model loading took`.
3. **Starting the engine:** active after step 2. Done when `GET http://127.0.0.1:$ENGINE_PORT/health` returns 200.
4. **Warming up:** active while the two warm-up requests run. Done when both succeed. Then `state` becomes `ready` and `/ready` returns 200.

If the engine process exits, or step 3 hasn't finished within 10 minutes, mark the current step `failed`.

## `POST /api/engine/restart`

Same endpoint as SPEC section 7. It resets the startup state to step 2 and the page carries on polling.
