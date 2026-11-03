# AGENTS.md — operating contract for voxcpm2-openai

This repository builds one container that serves **VoxCPM2 via vLLM-Omni** behind an
OpenAI-compatible `/v1/audio/speech` API plus the **Voice Studio** web UI.

**Authority:** `SPEC.md` and everything under `ui/` are the specification and are
**read-only**. `deploy/*.yaml` are validated reference configs; `deploy/voxcpm2-3090.yaml` and
`deploy/compose.yaml` must not change. Never edit spec/UI/reference files to make code pass.
If code and spec disagree, the spec wins and the discrepancy is escalated as a `Q-###`.

Read in this order before any task: `SPEC.md` → `docs/PLAN.md` → `docs/CONTRACTS.md` →
your task brief in `docs/tasks/<ID>.md`.

---

## 1. Roles

| Role | Agent | Model | May do |
|---|---|---|---|
| **Lead** | primary session (`lead`) | `litellm/frontier` | Talk to the human, plan, scaffold, dispatch, read diffs, review verdicts, **all commits/tags** |
| **Worker** | `worker` subagent | `litellm/worker` | Mechanical implementation inside its brief's `owns:` globs, tests, verification |
| **Reviewer** | `review` subagent | `litellm/frontier` (read-only) | Read code/diffs/spec and report findings; never edits, never runs mutating shell |

Rules:

- Workers are stateless: a brief plus `AGENTS.md` and `docs/CONTRACTS.md` must be enough context.
- Workers **never** commit, push, tag, merge, rebase, or run repo-wide formatters.
- Reviewers are **read-only**: no edits, no commits, no service restarts.
- Only the lead commits (conventional commits, one per task ID) and creates annotated tags.
- Only the lead talks to the human. A worker that hits an ambiguity stops and reports it; it does
  not invent a product decision.

## 2. Phase flow

```text
M0 Spikes (external host) ──► M1 Engine + /v1 ──► M2 Voice store + Studio API
                                     │                        │
                                     └────────► M3 UI ◄───────┘
                                                   │
                                        M4 Design flow (needs S2)
                                                   │
                                        M5 Delivery + cutover docs
```

Each phase is executed in **waves**. A wave contains every task whose `deps:` are complete and
whose `owns:` globs are disjoint from every other task in the same wave. Shared paths become a
dependency edge, never a merge conflict.

**Per wave:**

1. Lead confirms the ready set from `docs/PLAN.md` and re-checks `owns:` disjointness.
2. Lead dispatches every ready task **in one turn** as parallel `@worker` subagent calls. Each
   prompt contains only: brief path, hard boundaries (`owns:`), and "append a `## Report` with real
   command output".
3. Worker reads the brief, implements, runs `verify:` until green, and appends `## Report`
   (commands, exit codes, files changed, deviations, open questions) to its brief.
4. Lead reads the full diff, then invokes `@review` **twice in parallel** (spec/UI parity; and
   correctness/security/error paths/test gaps).
5. BLOCKER/MAJOR findings become new task IDs in the next wave. No inline hotfixes.
6. Lead updates `docs/PROGRESS.md` + `docs/MILESTONES.md`, commits one commit per task ID, and only
   then starts the next wave.

A wave does not advance until **every** task in it passes its `verify:`. A conflict means the graph
was wrong: fix `docs/PLAN.md`, not the merge.

## 3. Task brief format

Every task lives at `docs/tasks/<ID>.md` and starts with this header (machine-checkable):

```markdown
# <ID> — <title>

- **Milestone:** M1
- **Wave:** 2
- **owns:** app/routers/v1.py, tests/test_v1.py
- **deps:** M1-T1
- **verify:** python -m pytest tests/test_v1.py -q && ruff check app/routers/v1.py tests/test_v1.py
- **requirements:** R-012, R-013, R-017

## Context
<why this exists, exact contracts to consume>

## Steps
1. ... (test first where behaviour is non-trivial)

## Definition of done
- [ ] `verify:` green, output pasted in Report
- [ ] only `owns:` paths touched
- [ ] no TODO/stub/NotImplementedError left behind
```

`owns:` is a comma-separated list of file globs. A task may write **only** inside `owns:` plus its
own brief (`docs/tasks/<ID>.md`) for the Report. Reading anything is allowed. If a needed change is
outside `owns:`, stop and report `BLOCKED: needs <path> (owned by <ID>)`.

## 4. File ownership rules

- One path, one owner per wave. The scaffold (lead) creates shared contracts before wave 1;
  workers implement behind them.
- Shared contracts are frozen during a wave: `app/config.py`, `app/models.py`, `app/errors.py`,
  `app/audio.py`, `app/store.py`, `app/speech.py`, `app/main.py`, `docs/CONTRACTS.md`.
  Change requests go to the lead; they are new tasks, not silent edits.
- Tests travel with the module they test. A task owns its test files; nobody edits another task's
  tests to make them pass.
- Never touch: `SPEC.md`, `ui/**`, `deploy/voxcpm2-3090.yaml`, `deploy/compose.yaml`.
- No new dependency without a lead decision recorded in `docs/PROGRESS.md` decisions log.

## 5. Progress and milestones

- `docs/PLAN.md` — requirements inventory (`R-###`), ambiguity list (`Q-###`), milestones, task DAG,
  waves, `owns:`/`deps:`/`verify:`.
- `docs/PROGRESS.md` — current wave, task table with status (`todo|wip|pass|blocked`), blockers,
  append-only dated decisions log, next action, resume command.
- `docs/MILESTONES.md` — exit criteria per milestone, linked tasks, and the CI job that proves each.
- `docs/tasks/<ID>.md` — brief + append-only `## Report` with real command output.
- `NOTES.md` — spike findings S1–S6 (M0). Append-only.

Update `PROGRESS.md` and `MILESTONES.md` after every wave and before every commit. A milestone
closes only when: tasks done, `scripts/verify.sh` green locally, the CI image build is green, and
no unresolved BLOCKER.

## 6. Commands

| Command | Purpose |
|---|---|
| `/plan [milestone]` | Re-derive/refresh the task DAG and briefs for a milestone (lead only) |
| `/dispatch <wave>` | Dispatch all ready tasks of a wave as parallel workers |
| `/review <wave>` | Run both read-only reviewers over the wave diff |
| `/status` | Reconcile task table, blockers and next action into `docs/PROGRESS.md` |

## 7. Skills and MCP wiring

- **Skills:** `brainstorming` (requirements), `writing-plans` (PLAN.md), `test-driven-development`
  (worker steps), `verification-before-completion` (before any "done"), `systematic-debugging`
  (failing verify), `requesting-code-review`/`receiving-code-review` (review loop),
  `using-git-worktrees` if isolation is needed. Workers cannot load skills, so briefs carry the
  concrete steps.
- **context7 MCP** (`tools.context7.*`): resolve + fetch current library docs before using an API
  you are not certain about (FastAPI, httpx, soundfile, safetensors, uvicorn). Do not guess
  signatures.
- **Playwright MCP** (`tools.playwright.*`): render `ui/studio.html` and the real app, screenshot
  both at 1280 px and 390 px, compare layout/tokens/waveforms for the M3 review. This is the only
  accepted UI-parity evidence besides code diff.
- **Code search:** `grep`/`glob` first; read before writing.
- **GitLab:** repo remote is `git@gitlab.dotslash.io:jimmy/voxcpm2-openai.git`; CI (image build) lives in
  `.gitlab-ci.yml`. No GitLab MCP server is configured — do not invent one. Use `git` locally and
  report the pipeline URL after pushing; lead pushes only.
- **No secrets** in repo, briefs, logs, or CI output. Secrets arrive as CI variables / compose env.

## 8. Verification floor

- `scripts/verify.sh` is the single quality gate: format check, lint, typecheck, unit+integration
  tests with coverage, e2e, JS syntax, package build and Containerfile/s6 contract checks.
- CI is intentionally minimal (human decision 2026-11-02): it only builds and pushes the container
  image (plus opt-in deploy). Quality gates are local, not repeated in CI.
- Every non-trivial branch (parsing, money/security path, audio conversion, queueing) leaves at
  least one runnable test that fails if the logic breaks.
- The word "done" requires pasted command output. No exceptions.
- No stubs, `TODO`s, or `NotImplementedError` may cross a milestone boundary. If a spike is
  unverified, the code follows the spec's documented fallback and the risk is a `Q-###`.
