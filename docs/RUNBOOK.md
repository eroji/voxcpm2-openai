# RUNBOOK — voxcpm2-openai

Operations for build, deploy, acceptance, cutover, rollback, CI/CD activation and the M0 spikes.
Source of truth: `SPEC.md` §11–13, `deploy/compose.yaml`, `deploy/Containerfile`, `.gitlab-ci.yml`.
Target host is `vllm` (Podman + CDI GPU). **Never put real secrets in this file** — all values
below are placeholders.

Before pushing anything: `scripts/verify.sh` is the local gate that mirrors the CI pipeline
(format, lint, types, tests + coverage, e2e). Run it first; CI runs the same commands.

```bash
scripts/verify.sh          # full gate
scripts/verify.sh --fast   # skip e2e
```

## 1. Build & push

Locally (or on any machine with a registry login):

```bash
docker build -f deploy/Containerfile -t harbor.dotslash.io/dotslash/voxcpm2-openai:0.1.0 .
docker push harbor.dotslash.io/dotslash/voxcpm2-openai:0.1.0
```

On the `vllm` host use `podman build` / `podman push` with the same tags.

The CI `package` job builds `harbor.dotslash.io/dotslash/voxcpm2-openai:$CI_COMMIT_SHORT_SHA`
and pushes it. It needs two **masked** CI/CD variables (Settings → CI/CD → Variables):

| Variable | Purpose |
|---|---|
| `HARBOR_USER` | Harbor username for `harbor.dotslash.io` |
| `HARBOR_PASSWORD` | Harbor password/token (masked) |

**Without them** the job does not silently skip: it runs a static contract check instead
(greps `deploy/Containerfile` for the `COPY app/ /opt/app/` and `voxcpm2-3090.yaml` lines and
asserts `deploy/s6/engine` and `deploy/s6/app` exist). The pipeline stays green but **no image is
built or pushed** — set the variables to get real packaging. The job needs a docker-capable
runner (Q-015); if the runner lacks docker, restrict the job to `when: manual` and build on the
host with the commands above.

## 2. Run on 8881 (A/B next to qwen3-tts)

`deploy/compose.yaml` is the reference config; do not edit it in the repo. On the host:

```bash
mkdir -p /opt/voxcpm2-openai/deploy /opt/voxcpm2-openai/data
# copy deploy/compose.yaml and deploy/voxcpm2-3090.yaml from the repo into /opt/voxcpm2-openai/deploy
cd /opt/voxcpm2-openai
podman-compose -f deploy/compose.yaml up -d
podman logs -f voxcpm2-openai        # engine load takes minutes; healthcheck start_period is 300s
curl -fsS http://127.0.0.1:8881/ready && echo READY
```

Volumes and GPU (from `deploy/compose.yaml`):

| Mount | Meaning |
|---|---|
| `./data:/data` | the only stateful path: voices, takes, backups, settings |
| `/models:/models:ro` | weights at `/models/openbmb/VoxCPM2` |
| `/opt/qwen3-tts/data/speakers:/import/qwen3-speakers:ro` | one-time lossless import of the eight Qwen3 voices |
| `devices: nvidia.com/gpu=GPU-79ad6f3b-…` | CDI GPU 1 (RTX 3090), shared with `whisper-stt` + TEI |
| `shm_size: 8g` | engine shared memory |

Back up the data dir before every cutover and periodically:

```bash
tar czf /backup/voxcpm2-data-$(date +%F).tar.gz -C /opt/voxcpm2-openai data
```

(The app also writes its own backups under `/data/backups` before voice edits/deletes.)

Auth env vars — uncomment in the compose `environment:` block and set real values **on the host
only**. Placeholders shown; never commit real values:

```yaml
environment:
  STUDIO_AUTH: "admin:<pick-a-strong-password>"     # basic auth for / and /api/*
  API_KEYS: "<generate: openssl rand -hex 32>"      # bearer token(s) for /v1/*, comma-separated
```

`/health` and `/ready` stay open; `/v1/*` is unauthenticated unless `API_KEYS` is set (SPEC §10).

## 3. Acceptance on 8881

Run the smoke script (from the repo, against the running deployment):

```bash
BASE_URL=http://127.0.0.1:8881 scripts/smoke.sh
# with auth configured, pass it via env (never on the command line):
API_KEY=<litellm…key> STUDIO_AUTH=admin:<host-value> BASE_URL=http://127.0.0.1:8881 scripts/smoke.sh
```

It checks `/health`, `/ready`, `/v1/models`, `/v1/audio/voices`, a 48 kHz WAV with
`X-Sample-Rate: 48000`, streamed PCM bytes within 2 s, and `/api/engine` `ready:true`.

All eight imported voices (SPEC §5, §13):

```bash
for v in ashley-en ashley-ja ashley-kr ashley-zh alison-en bennett-en jack-en mia-en; do
  VOICE="$v" BASE_URL=http://127.0.0.1:8881 scripts/smoke.sh || break
done
```

Streamed first-chunk budget — warm first chunk ≤ 300 ms (measured ~154 ms, SPEC §2/§13):

```bash
curl -s http://127.0.0.1:8881/api/engine | python3 -c 'import json,sys; d=json.load(sys.stdin); print("first_audio_ms:", d["first_audio_ms"]); assert d["first_audio_ms"] is not None and d["first_audio_ms"] <= 300, "over budget"'
```

Queue behaviour — capacity is 2 active + 16 queued (Q-016); a burst of 20 must succeed or return
clean `429` with `Retry-After`, never 5xx:

```bash
for i in $(seq 20); do
  curl -s -o /dev/null -w '%{http_code}\n' -H 'Content-Type: application/json' \
    -d '{"model":"tts-1","voice":"ashley-en","input":"Queue probe."}' \
    http://127.0.0.1:8881/v1/audio/speech &
done; wait
```

VRAM — engine ≤ 10 GB while `whisper-stt` and TEI keep running on the same card:

```bash
nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader,nounits -i 0
```

Also verify by hand per SPEC §13: clone flow (upload → transcribe → save → usable by name on
`/v1`), design flow (description → candidates → save), and UI parity with `ui/studio.html`.

## 4. Cutover to 8880

Only after §3 is fully green and the data dir is backed up (SPEC §11):

```bash
# 1. stop the old service (do not remove it)
podman stop qwen3-tts-openai

# 2. move the new service to 8880: in /opt/voxcpm2-openai/deploy/compose.yaml change
#    ports:  "8881:8000"  ->  "8880:8000"
cd /opt/voxcpm2-openai && podman-compose -f deploy/compose.yaml up -d
curl -fsS http://127.0.0.1:8880/ready && echo READY

# 3. point the LiteLLM `tts` alias at http://vllm.dotslash.io:8880/v1 and reload LiteLLM
```

Consumers to check unchanged: **Open WebUI** TTS, **Hermes**, the **Wyoming bridge** — one spoken
request each, by voice name. If callers use `/v1` with `API_KEYS` set, configure the same key in
LiteLLM.

## 5. Rollback

```bash
# 1. point the LiteLLM `tts` alias back to the qwen3 target and reload LiteLLM
# 2. stop the new service and restart the old one
cd /opt/voxcpm2-openai && podman-compose -f deploy/compose.yaml down
podman start qwen3-tts-openai
curl -fsS http://127.0.0.1:8880/health && echo BACK
```

Keep `/opt/qwen3-tts` untouched until the new service has run cleanly for a week; only then may
it be archived. Voices created on voxcpm2-openai live in `/opt/voxcpm2-openai/data` and survive
a rollback — restore them from the backup tarball if needed.

## 6. Turning on CD

**CD is off by default and has not been exercised on this host.** With no `DEPLOY_TARGET` set, the
deploy jobs are never created. To enable, set CI/CD variables (masked where marked):

| Variable | Value | Notes |
|---|---|---|
| `DEPLOY_TARGET` | `staging` \| `production` | nothing deploys without it |
| `DEPLOY_HOST` | ssh target, e.g. `root@vllm.dotslash.io` | |
| `DEPLOY_PATH` | e.g. `/opt/voxcpm2-openai` | must contain `deploy/compose.yaml` |
| `DEPLOY_SSH_KEY` | private key (masked) | public half in the host's `authorized_keys` |

- `staging`: runs automatically on default-branch pipeline success.
- `production`: requires a `vX.Y.Z` tag **and** a manual approval on the `deploy:production` job;
  it finishes with a `curl -fsS http://127.0.0.1:8880/ready` smoke check over ssh.

Caveat before enabling: the jobs pass `DEPLOY_IMAGE=$CI_COMMIT_SHORT_SHA` into `docker compose`,
but the reference `deploy/compose.yaml` pins the image tag literally (`:0.1.0`). Either parameterise
the image line on the host copy or update the tag before trusting CD to ship a specific build.

## 7. M0 spikes

Findings belong in `NOTES.md` at the repo root (append-only, one section per spike S1–S6), under
its existing `## S<n>` headings. Each spike is a short script against the test container
`voxcpm2-test` serving VoxCPM2 on `127.0.0.1:8881` with `deploy/voxcpm2-3090.yaml`. Commands
(SPEC §12):

```bash
E=http://127.0.0.1:8881/v1/audio/speech
# S1 style control: does `instructions` change the audio? diff the two files.
curl -s -o s1a.wav -H 'Content-Type: application/json' -d '{"model":"openbmb/VoxCPM2","input":"The quick brown fox.","response_format":"wav"}' $E
curl -s -o s1b.wav -H 'Content-Type: application/json' -d '{"model":"openbmb/VoxCPM2","input":"The quick brown fox.","instructions":"calm, slow","response_format":"wav"}' $E
cmp -s s1a.wav s1b.wav && echo "S1: instructions IGNORED" || echo "S1: instructions HONOURED"

# S2 voice design: description with no ref_audio; vary seed, compare speakers.
curl -s -o s2.wav -H 'Content-Type: application/json' -d '{"model":"openbmb/VoxCPM2","input":"Hello there.","description":"a warm middle-aged woman","seed":1,"response_format":"wav"}' $E

# S3 streaming format: byte count -> seconds at s16le 48 kHz vs the WAV version.
curl -sN -H 'Content-Type: application/json' -d '{"model":"openbmb/VoxCPM2","input":"The quick brown fox.","response_format":"pcm","stream":true}' $E | wc -c

# S4 sampling knobs: cfg / steps / seed accepted, or 4xx?
curl -s -o /dev/null -w '%{http_code}\n' -H 'Content-Type: application/json' -d '{"model":"openbmb/VoxCPM2","input":"Probe.","response_format":"wav","cfg":2.0,"steps":10,"seed":7}' $E

# S5 warm-up: time the first request after container start, then a second one.
podman restart voxcpm2-test && sleep 5
time curl -s -o s5a.wav -H 'Content-Type: application/json' -d '{"model":"openbmb/VoxCPM2","input":"Warm up.","response_format":"wav"}' $E
time curl -s -o s5b.wav -H 'Content-Type: application/json' -d '{"model":"openbmb/VoxCPM2","input":"Warm again.","response_format":"wav"}' $E

# S6 Qwen3 shim field compatibility: old caller shapes must still work (SPEC §6).
curl -s -o s6.wav -H 'Content-Type: application/json' -d '{"model":"tts-1","voice":"ashley-en","input":"Shim probe.","response_format":"wav","task_type":"bible_tts"}' $E
```

Record each result (honoured / ignored / 4xx, byte counts, timings) under a `## S<n>` heading in
`NOTES.md`, and adjust the design per SPEC §12 if a spike fails.
