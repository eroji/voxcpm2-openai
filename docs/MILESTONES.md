# Milestones

Status words: `todo`, `wip`, `pass`, `blocked`. A milestone closes only when all tasks are `pass`,
`scripts/verify.sh` is green, CI is green, and no BLOCKER is open.

| Milestone | Exit criteria | Tasks | Proving CI job | Status |
|---|---|---|---|---|
| **M0 Spikes** | S1–S6 run against the `vllm` host; findings + command output in `NOTES.md`; design adjusted where a spike failed | M0-T1 | `spikes` (script, host-run — not a GitLab job) | **blocked (external, B-001)** |
| **M1 Engine + `/v1`** | `/v1/audio/speech` returns WAV 48 kHz; all five formats + streaming; voice resolution, headers, OpenAI errors; 2+16 queue with 429; models/voices endpoints; warm-up gates `/ready`; s6 services wired | M1-T1…T15 | `test`, `e2e` | **pass locally** (CI pending push) |
| **M2 Voice store + Studio API** | voices CRUD + audio, clone (validation/normalization/consent), transcribe, import (all 8 names), takes list/audio/wave/save-as-voice/delete, engine status/warmup/restart; backups before destructive changes | M2-T1…T13 | `test` | **pass locally** |
| **M3 UI** | five hash-routed screens wired to the API; tokens/layout match `ui/studio.html`; smooth 72-bucket waveforms; keyboard-only navigation; inline errors | M3-T1…T10 | `e2e` (10 screenshots at 1280/390 in `test-results/`) + lead review | **pass locally** |
| **M4 Design flow** | description → 1–4 candidates (distinct seeds) → select → save → voice usable by name over `/v1` | M4-T1, M4-T2 | `test`, `e2e` | **pass locally** (S2 transport still Q-002) |
| **M5 Delivery + cutover** | CI green (checks/test/e2e/build/package), container builds, runbook + rollback + CD activation documented, acceptance suite present | M5-T1…T3 | `build`, `package` | **partial** — delivery files pass locally; CI/container/cutover external |

## Known external blockers

- **M0**: no VoxCPM2 engine reachable from the development machine and the local GPU is not the
  `vllm` RTX 3090 host. Spike scripts are delivered; a human must run them on `vllm` and paste
  results into `NOTES.md` (Q-001). M0 cannot be marked green here by design.
- **M5 `package`**: the real container build needs Harbor credentials (`HARBOR_USER`,
  `HARBOR_PASSWORD`) as masked CI variables. Without them CI runs the Containerfile contract
  check instead; this is documented, not hidden.
- **M5 `deploy:*`**: intentionally excluded by `rules`; CD is not enabled (Q-015).
