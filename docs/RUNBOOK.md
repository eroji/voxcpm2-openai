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

The CI `package` job builds on the default branch (image tag `$CI_COMMIT_SHORT_SHA`) and on
`vX.Y.Z` release tags (image tag `${CI_COMMIT_TAG#v}`, i.e. `X.Y.Z`) and pushes to
`harbor.dotslash.io/dotslash/voxcpm2-openai`. It needs two **masked** CI/CD variables
(Settings → CI/CD → Variables) — the same names as the notely repo:

| Variable | Purpose |
|---|---|
| `HARBOR_USER` | Harbor username/robot with push rights to `dotslash/voxcpm2-openai` |
| `HARBOR_TOKEN` | Its password or robot token, for `docker login` (masked) |

**Without them** the job does not silently skip: it runs a static contract check instead
(greps `deploy/Containerfile` for the `COPY app/ /opt/app/` and `voxcpm2-3090.yaml` lines and
asserts `deploy/s6/engine` and `deploy/s6/app` exist). The pipeline stays green but **no image is
built or pushed** — set the variables to get real packaging. The job needs a docker-capable
runner (Q-015); if the runner lacks docker, restrict the job to `when: manual` and build on the
host with the commands above.

## 2. Run on 8881 (A/B next to qwen3-tts)

`deploy/compose.yaml` is the reference config; do not edit it in the repo. On the host:

```bash
mkdir -p /opt/voxcpm2-openai/deploy/data
# copy deploy/compose.yaml and deploy/voxcpm2-3090.yaml from the repo into /opt/voxcpm2-openai/deploy
cd /opt/voxcpm2-openai/deploy
podman-compose -f compose.yaml up -d
podman logs -f voxcpm2-openai        # engine load takes minutes; healthcheck start_period is 300s
curl -fsS http://127.0.0.1:8881/ready && echo READY
```

Compose resolves relative bind mounts against the **compose file's directory**, so `./data` is
`/opt/voxcpm2-openai/deploy/data` on the host (not `/opt/voxcpm2-openai/data`). Every backup and
restore path below must use that directory.

Volumes and GPU (from `deploy/compose.yaml`):

| Mount | Meaning |
|---|---|
| `./data:/data` = `/opt/voxcpm2-openai/deploy/data` | the only stateful path: voices, takes, backups, settings |
| `/models:/models:ro` | weights at `/models/openbmb/VoxCPM2` |
| `/opt/qwen3-tts/data/speakers:/import/qwen3-speakers:ro` | one-time lossless import of the eight Qwen3 voices |
| `devices: nvidia.com/gpu=GPU-79ad6f3b-…` | CDI GPU 1 (RTX 3090, **host index 1**), shared with `whisper-stt` + TEI |
| `shm_size: 8g` | engine shared memory |

`STT_URL` is confirmed as `http://vllm.dotslash.io:8890/v1` (whisper-stt, human-verified
2026-11-02) — that is the value in `deploy/compose.yaml`.

Back up the data dir before every cutover and periodically:

```bash
mkdir -p /backup
tar czf /backup/voxcpm2-data-$(date +%F).tar.gz -C /opt/voxcpm2-openai/deploy data
```

Restore with `tar xzf <tarball> -C /opt/voxcpm2-openai/deploy` (stop the container first).

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
`X-Sample-Rate: 48000` **and `X-RTF` present and ≤ 0.2** (SPEC §13), streamed PCM bytes within
2 s, `/api/engine` `ready:true`, and a 20-request burst (below) — every call uses the same
`API_KEY`/`STUDIO_AUTH` env auth as here, so run it with auth configured whenever the server has
it.

All eight imported voices (SPEC §5, §13) — the loop must fail loudly if any voice fails:

```bash
rc=0
for v in ashley-en ashley-ja ashley-kr ashley-zh alison-en bennett-en jack-en mia-en; do
  VOICE="$v" BASE_URL=http://127.0.0.1:8881 scripts/smoke.sh || { rc=1; break; }
done
exit $rc
```

Warm RTF ≤ 0.2 (SPEC §13, measured ~0.14, SPEC §2) — asserted inside `scripts/smoke.sh`; the
explicit host probe, authenticated the same way as every other `/v1` call:

```bash
cfg=$(mktemp); [ -n "${API_KEY:-}" ] && printf 'header = "Authorization: Bearer %s"\n' "$API_KEY" > "$cfg"
curl -s --config "$cfg" -m 60 -D - -o /dev/null -H 'Content-Type: application/json' \
  -d '{"model":"tts-1","voice":"ashley-en","input":"Real-time factor probe."}' \
  http://127.0.0.1:8881/v1/audio/speech | grep -i '^x-rtf:'   # must be ≤ 0.2
```

Streamed first-chunk budget — warm first chunk ≤ 300 ms (measured ~154 ms, SPEC §2/§13):

```bash
curl -s http://127.0.0.1:8881/api/engine | python3 -c 'import json,sys; d=json.load(sys.stdin); print("first_audio_ms:", d["first_audio_ms"]); assert d["first_audio_ms"] is not None and d["first_audio_ms"] <= 300, "over budget"'
```

Queue behaviour — capacity is 2 active + 16 queued (Q-016), so a burst of 20 concurrent requests
admits **18** (2 synthesizing + 16 queued) and returns **2× `429`** with `Retry-After`, never 5xx.
`scripts/smoke.sh` asserts exactly this with the same auth as its other calls; the explicit host
probe (reuse `$cfg` from the RTF probe above — it carries the `API_KEYS` bearer token when set):

```bash
for i in $(seq 20); do
  curl -s --config "$cfg" -m 60 -o /dev/null -w '%{http_code}\n' \
    -H 'Content-Type: application/json' \
    -d '{"model":"tts-1","voice":"ashley-en","input":"Queue probe."}' \
    http://127.0.0.1:8881/v1/audio/speech &
done; wait
# expect: 18 lines "200", 2 lines "429"; 429 responses must carry Retry-After
```

VRAM — engine ≤ 10 GB while `whisper-stt` and TEI keep running on the same card. On the **host**
the speech card is GPU **index 1** (SPEC §2); index 0 is a different card:

```bash
nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader,nounits -i 1
# or address it by UUID, immune to index changes:
nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader,nounits \
  -i GPU-79ad6f3b-cc71-90a6-2430-286e1c6c9ec2
```

**Inside the container** the CDI-visible card is device **0** — that is what `/api/engine` reports:

```bash
podman exec voxcpm2-openai nvidia-smi --query-gpu=name,memory.used,memory.total \
  --format=csv,noheader,nounits -i 0
```

Remaining hardware checks — proven on the `vllm` host, **not** by the fake-engine pytest suite
(each name is the corresponding `pytest.mark.skip` in `tests/test_acceptance.py`):

| Check | Where | Skip test |
|---|---|---|
| Warm RTF ≤ 0.2 + VRAM ≤ 10 GB + card sharing | RTF probe and `nvidia-smi` above | `test_hardware_rtf_and_vram` (Q-001 / M0) |
| External clients (LiteLLM, Open WebUI, Hermes, Wyoming) | §4 cutover | `test_acceptance_external_clients_unchanged` |
| Eight voices spot-checked by ear | human listener | `test_acceptance_voice_spot_check` |
| Container build | CI `package` job | `test_acceptance_container_builds` |
| UI parity + keyboard | Playwright e2e | `test_acceptance_ui_parity_and_keyboard` |

Also verify by hand per SPEC §13: clone flow (upload → transcribe → save → usable by name on
`/v1`), design flow (description → candidates → save), and UI parity with `ui/studio.html`.

## 4. Cutover to 8880

Only after §3 is fully green and the data dir is backed up (SPEC §11):

```bash
# 1. stop the old service (do not remove it)
podman stop qwen3-tts-openai

# 2. move the new service to 8880: in /opt/voxcpm2-openai/deploy/compose.yaml change
#    ports:  "8881:8000"  ->  "8880:8000"
cd /opt/voxcpm2-openai/deploy && podman-compose -f compose.yaml up -d
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
cd /opt/voxcpm2-openai/deploy && podman-compose -f compose.yaml down
podman start qwen3-tts-openai
curl -fsS http://127.0.0.1:8880/health && echo BACK
```

Keep `/opt/qwen3-tts` untouched until the new service has run cleanly for a week; only then may
it be archived. Voices created on voxcpm2-openai live in `/opt/voxcpm2-openai/deploy/data` and
survive a rollback — restore them from the backup tarball (§2) if needed.

## 6. Turning on CD

**CD is off by default and has not been exercised on this host.** With no `DEPLOY_TARGET` set, the
deploy jobs are never created. The pattern is the proven one from the notely repo. Set these CI/CD
variables (Settings → CI/CD → Variables):

| Variable | Value | Notes |
|---|---|---|
| `DEPLOY_TARGET` | `staging` \| `production` | nothing deploys without it |
| `DEPLOY_HOST` | default `vllm.dotslash.io` | set in `.gitlab-ci.yml`; override only if the host moves |
| `DEPLOY_USER` | default `deploy` | key-only account in the `docker` group, owns `$DEPLOY_PATH`; public half of `DEPLOY_SSH_KEY` in `~deploy/.ssh/authorized_keys` |
| `DEPLOY_PATH` | default `/opt/voxcpm2-openai` | must contain `deploy/compose.yaml` **and** `deploy/compose.deploy.yaml` |
| `DEPLOY_SSH_KEY` | private key, one line: `base64 -w0 id_ed25519` (masked, protected) | decoded to `~/.ssh/id_ed25519` in the job |
| `HARBOR_USER` / `HARBOR_TOKEN` | same as §1 | the target does its own `docker login` over stdin, so `HARBOR_TOKEN` must be **protected** to reach tag pipelines |

- `staging`: runs automatically on default-branch pipeline success, deploying the
  `$CI_COMMIT_SHORT_SHA` image the `package` job just pushed.
- `production`: releases are tags. `git tag vX.Y.Z && git push --follow-tags` builds image
  `X.Y.Z`, then the manual `deploy:production` job (approve it in the pipeline) pins exactly that
  image — never a later build — and finishes with a `curl -fsS http://127.0.0.1:8880/ready`
  health gate over ssh.

The image is pinned through `deploy/compose.deploy.yaml` (override:
`image: ${DEPLOY_IMAGE:?DEPLOY_IMAGE is required}`), so the frozen reference `deploy/compose.yaml`
keeps its literal `:0.1.0` tag; copy both files to `$DEPLOY_PATH/deploy` on the host.

Caveats before enabling:

- The deploy jobs run `docker compose` over ssh, but the `vllm` host uses **Podman**: this only
  works if a `podman-docker` wrapper is installed there. Otherwise change the deploy commands to
  `podman-compose -f deploy/compose.yaml -f deploy/compose.deploy.yaml pull/up -d` (a
  `.gitlab-ci.yml` change — new task).
- Port 8880 in the health gate assumes the §4 cutover has happened; before cutover the container
  serves 8881.

## 7. M0 spikes

Findings belong in `NOTES.md` at the repo root (append-only, one section per spike S1–S6), under
its existing `## S<n>` headings. Each spike is a tested script against the test container
`voxcpm2-test` serving VoxCPM2 on `127.0.0.1:8881` with `deploy/voxcpm2-3090.yaml` (SPEC §12).
Run them from the repo root on the `vllm` host — the scripts are the single source of truth, do
not hand-roll curls:

```bash
B=http://127.0.0.1:8881
A=app/assets/warmup.wav   # 1.5 s tone, pairs with --ref-text "Warm-up." (Q-014);
                          # a real 5-10 s cloned voice + its transcript is a better probe
python scripts/spikes/s1_style.py  --base $B --audio $A   # style: baseline×2 noise floor, then instructions / syntax
python scripts/spikes/s2_design.py --base $B              # description-only on purpose: sends no ref_audio
python scripts/spikes/s3_stream.py --base $B --audio $A   # stream bytes → s16le@48k vs WAV duration
python scripts/spikes/s4_knobs.py  --base $B --audio $A   # cfg/steps/seed payload shape accepted?
python scripts/spikes/s5_warmup.py --base $B --audio $A   # run IMMEDIATELY after: podman restart voxcpm2-test
python scripts/spikes/s6_shim.py                          # prints the `podman exec qwen3-tts-openai …`
                                                          # capture command; re-run with --shim-json fixtures
```

`--audio`/`--ref-text` defaults match the flags above, so the flags are optional; pass a real
voice clip for meaningful S1/S3/S4 results (see `scripts/spikes/README.md`). Inside the production
container use `--base http://127.0.0.1:8102`.

Record each result (honoured / ignored / 4xx, byte counts, timings) under a `## S<n>` heading in
`NOTES.md`, and adjust the design per SPEC §12 if a spike fails.
