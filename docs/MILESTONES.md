# Milestones

Status words: `todo`, `wip`, `pass`, `blocked`. A milestone closes only when all tasks are `pass`,
`scripts/verify.sh` is green, the CI image build is green, and no BLOCKER is open.

| Milestone | Exit criteria | Tasks | Proving job | Status |
|---|---|---|---|---|
| **M0 Spikes** | S1–S6 run against the `vllm` host; findings + command output in `NOTES.md`; design adjusted where a spike failed | M0-T1 | `spikes` (script, host-run — not a GitLab job) | **blocked (external, B-001)** |
| **M1 Engine + `/v1`** | `/v1/audio/speech` returns WAV 48 kHz; all five formats + streaming; voice resolution, headers, OpenAI errors; 2+16 queue with 429; models/voices endpoints; warm-up gates `/ready`; s6 services wired | M1-T1…T15 | `scripts/verify.sh` | **pass locally** |
| **M2 Voice store + Studio API** | voices CRUD + audio, clone (validation/normalization/consent), transcribe, takes list/audio/wave/save-as-voice/delete, engine status/warmup/restart; backups before destructive changes | M2-T1…T17 | `scripts/verify.sh` | **pass locally** |
| **M3 UI** | five hash-routed screens wired to the API; tokens/layout match `ui/studio.html`; smooth 72-bucket waveforms; keyboard-only navigation; inline errors | M3-T1…T16 | `scripts/verify.sh` e2e (startup + six-screen screenshots) + lead review | **pass locally** |
| **M4 Design flow** | description → 1–4 candidates (distinct seeds) → select → save → voice usable by name over `/v1` | M4-T1, M4-T2 | `scripts/verify.sh` | **pass locally** (S2 transport still Q-002) |
| **M5 Delivery + cutover** | image builds in CI, tag deploy to vllm works, runbook + rollback documented, acceptance suite present | M5-T1…T5 | CI `build`, `deploy` | **partial** — CI build/deploy need protected vars; cutover external |

## Known external blockers

- **M0**: no VoxCPM2 engine reachable from the development machine and the local GPU is not the
  `vllm` RTX 3090 host. Spike scripts are delivered; a human must run them on `vllm` and paste
  results into `NOTES.md` (Q-001). M0 cannot be marked green here by design.
- **M5 `build`**: the CI image build needs Harbor credentials (`HARBOR_USER`, `HARBOR_TOKEN`) as
  masked CI variables, because the base image and push target are private.
- **M5 `deploy:*`**: intentionally excluded by `rules`; CD is not enabled (Q-015).
