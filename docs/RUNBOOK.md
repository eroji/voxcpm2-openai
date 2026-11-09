# RUNBOOK — voxcpm2-openai

Operations for build, deploy, acceptance, cutover, rollback, CI/CD activation and the M0 spikes.
Source of truth: `SPEC.md` §11–13, `deploy/compose.yaml`, `deploy/Containerfile`, `.gitlab-ci.yml`.
Target host is `vllm` (Podman + CDI GPU). **Never put real secrets in this file** — all values
below are placeholders.

Before pushing anything: `scripts/verify.sh` is the quality gate (format, lint, types, tests +
coverage, e2e, JS syntax, build, package contract). CI only builds/pushes the image.

```bash
scripts/verify.sh          # full gate
scripts/verify.sh --fast   # skip e2e
```

## 1. Build & push

Locally (or on any machine with a registry login):

```bash
docker build -f deploy/Containerfile \
  -t harbor.dotslash.io/dotslash/voxcpm2-openai:v0.3.12 \
  -t harbor.dotslash.io/dotslash/voxcpm2-openai:latest .
docker push harbor.dotslash.io/dotslash/voxcpm2-openai:v0.3.12
docker push harbor.dotslash.io/dotslash/voxcpm2-openai:latest
```

On the `vllm` host use `podman build` / `podman push` with the same tags.

The CI `build` job runs **only on `vX.Y.Z` tags**. It pushes two tags to
`harbor.dotslash.io/dotslash/voxcpm2-openai`: the release tag itself (`vX.Y.Z`, with the `v`) and
`latest`. Pushes to `main` and ordinary commits create no pipeline. It needs two **masked** CI/CD variables
(Settings → CI/CD → Variables) — the same names as the notely repo:

| Variable | Purpose |
|---|---|
| `HARBOR_USER` | Harbor username/robot with push rights to `dotslash/voxcpm2-openai` |
| `HARBOR_TOKEN` | Its password or robot token, for `docker login` (masked) |

**Without them** the `build` job fails before `docker build` with an explicit message naming
`HARBOR_USER`/`HARBOR_TOKEN`. Because the variables are instance-wide and marked **Protected**,
the release tag must also be protected (`Settings → Repository → Protected tags → v*`) or the
variables must be unprotected. The job needs a docker-capable runner (Q-015).

## 2. Run on 8881 (A/B next to qwen3-tts)

The host layout is `/opt/voxcpm2-openai/{compose.yaml,.env,data}`. The image tag lives only in
`.env`; `compose.yaml` reads `${VOXCPM2_IMAGE}`. `./data` resolves to
`/opt/voxcpm2-openai/data` (the only stateful path). CI updates both files and recreates the pod;
manual equivalent:

```bash
IMAGE=harbor.dotslash.io/dotslash/voxcpm2-openai:v0.3.12
ssh jumpbox "sudo -n ssh vllm 'cat > /opt/voxcpm2-openai/compose.yaml'" < deploy/compose.yaml
ssh jumpbox "sudo -n ssh vllm \"
  cd /opt/voxcpm2-openai
  printf 'VOXCPM2_IMAGE=%s\n' '$IMAGE' > .env
  podman-compose -f compose.yaml up -d --force-recreate --remove-orphans --pull
  curl -fsS http://127.0.0.1:8881/ready && echo READY
\""
podman logs -f voxcpm2-openai   # via ssh; expect "service engine: starting" + VoxCPM2
```

By default the engine uses the YAML baked into the image
(`/opt/app/deploy/voxcpm2-3090.yaml`). To tune it on the host without rebuilding, copy
`deploy/voxcpm2-3090.yaml` to `/opt/voxcpm2-openai/voxcpm2-3090.yaml`, add
`VOXCPM2_DEPLOY_CONFIG=/opt/voxcpm2-openai/voxcpm2-3090.yaml` to `.env`, and recreate with both
compose files (`-f compose.yaml -f compose.config.yaml`). Remove that line to fall back.

If `[base]`/Qwen3-TTS appears, the image still carries the old Qwen3 supervisor; rebuild from this
repo. A `--port ''` error means an old image without `with-contenv` s6 scripts. A stale
`data/engines.json` from the old supervisor is ignored.

Volumes and GPU:

| Mount | Meaning |
|---|---|
| `./data:/data` = `/opt/voxcpm2-openai/data` | voices, takes, backups, settings, languages.json |
| `/models:/models:ro` | weights at `/models/openbmb/VoxCPM2` |
| `devices: nvidia.com/gpu=GPU-79ad6f3b-…` | CDI GPU 1 (RTX 3090, host index 1), shared with whisper-stt + TEI |
| `shm_size: 8g` | engine shared memory |

`STT_URL` is `http://vllm.dotslash.io:8890/v1`. HF downloads are off by default
(`HF_HUB_OFFLINE=1`); export `HF_TOKEN` on the host and set `HF_HUB_OFFLINE=0` in `.env` to allow
authenticated cache downloads.

**Guidance and diffusion steps.** VoxCPM2's CFG and steps are server-wide and read at engine
start (vLLM-Omni ignores per-request values, NOTES.md S4). Set them in `.env` on the host, then
restart the container:

```bash
VOXCPM2_CFG_VALUE=2.5            # default 2.0; higher follows descriptions/voices more strictly
VOXCPM2_INFERENCE_TIMESTEPS=10   # default 10; at least 2; more = slower, more detail
```

The engine runner writes `data/engine-deploy.yaml` (the reference deploy config plus these
values) and logs `[engine] VoxCPM2 runtime settings: {...}`. An invalid value stops the engine
with `[engine] invalid configuration: ...` in `data/logs/engine.log`. The image backports
vllm-omni PR #7010 so the values take effect (`deploy/patches/voxcpm2_runtime_knobs.py`).

Languages are app-level: `data/languages.json` holds enabled/default/presets, while
`/opt/app/deploy/supported-languages.json` (baked into the image) is the engine set.

The first browser load shows the startup page until `/ready` 200; `/api/engine/startup` reports the
four steps and the merged engine/app log (`data/logs/engine.log`). s6 retries after 30/60/120 s,
then waits for a manual restart.

Back up the data dir before every cutover and periodically:

```bash
mkdir -p /backup
tar czf /backup/voxcpm2-data-$(date +%F).tar.gz -C /opt/voxcpm2-openai data
```

Restore with `tar xzf <tarball> -C /opt/voxcpm2-openai` (stop the container first). The app also
writes `/data/backups` before voice/language edits.

Auth env vars go in `.env` on the host only (placeholders):
`STUDIO_AUTH=user:password`, `API_KEYS=key1,key2`. Do not commit real values.

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
| Container build | CI `build` job / `scripts/verify.sh` package contract | `test_acceptance_container_builds` |
| UI parity + keyboard | Playwright e2e | `test_acceptance_ui_parity_and_keyboard` |

Also verify by hand per SPEC §13: clone flow (upload → transcribe → save → usable by name on
`/v1`), design flow (description → candidates → save), and UI parity with `ui/studio.html`.

## 4. Cutover to 8880

Only after §3 is fully green and the data dir is backed up (SPEC §11):

```bash
# 1. stop the old service (do not remove it)
podman stop qwen3-tts-openai
# 2. change ports "8881:8000" -> "8880:8000" in the host copy of /opt/voxcpm2-openai/compose.yaml
cd /opt/voxcpm2-openai && podman-compose -f compose.yaml up -d --force-recreate
curl -fsS http://127.0.0.1:8880/ready && echo READY
# 3. point the LiteLLM `tts` alias at http://vllm.dotslash.io:8880/v1 and reload LiteLLM
```

Consumers to check unchanged: Open WebUI, Hermes, the Wyoming bridge — one spoken request each,
by voice name.

## 5. Rollback

```bash
# 1. point LiteLLM back to the qwen3 target and reload
# 2. stop the new service and restart the old one
cd /opt/voxcpm2-openai && podman-compose -f compose.yaml down
podman start qwen3-tts-openai
curl -fsS http://127.0.0.1:8880/health && echo BACK
```

Keep `/opt/qwen3-tts` untouched for a week. Voices created on voxcpm2-openai live in
`/opt/voxcpm2-openai/data` and survive rollback — restore from the backup tarball (§2) if needed.

## 6. Deploy (CD)

A `vX.Y.Z` tag pipeline builds the image, then the `deploy` job ships it directly to `vllm`:

```
runner --(DEPLOY_SSH_KEY)--> deploy@vllm.dotslash.io
```

The job logs the target into Harbor over stdin, copies `deploy/compose.yaml` to
`/opt/voxcpm2-openai/compose.yaml`, writes
`VOXCPM2_IMAGE=harbor.dotslash.io/dotslash/voxcpm2-openai:<tag>` into `.env` (preserving other
keys), runs `podman-compose up -d --force-recreate --pull`, then waits up to 10 minutes for
`http://127.0.0.1:8881/ready`.

Required **protected** CI variables (Settings → CI/CD → Variables):

| Variable | Value |
|---|---|
| `DEPLOY_SSH_KEY` | base64 -w0 of the private key whose public half is in `deploy@vllm:~/.ssh/authorized_keys` |
| `HARBOR_USER` / `HARBOR_TOKEN` | Harbor credentials; the job logs `deploy@vllm` in over stdin |
| `DEPLOY_HOST` | `vllm.dotslash.io` (default in the job) |
| `DEPLOY_PATH` | `/opt/voxcpm2-openai` (default in the job) |
| `VOXCPM2_DEPLOY_CONFIG` | optional host YAML path in `.env`; set it to opt into `compose.config.yaml` |

Release: `git tag vX.Y.Z && git push origin vX.Y.Z`; the tag must be protected so protected
variables are available. Rollback a bad deploy with §5, or write a previous tag's image into `.env`
and re-run `podman-compose up -d --force-recreate`.

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
