---
description: Run both read-only reviewers over a wave and triage findings
agent: lead
---

Review wave $ARGUMENTS (default: last completed wave in `docs/PROGRESS.md`).

Confirm the tree is clean of uncommitted worker leftovers and `scripts/verify.sh` is green. Then
invoke the `review` subagent **twice in parallel**:

- Reviewer A — spec conformance and UI parity: check every `R-###` the wave claims against
  `SPEC.md`; compare rendered UI to `ui/studio.html`/`ui/tokens.css` using the Playwright MCP
  (1280 px and 390 px screenshots).
- Reviewer B — correctness, security, error paths, test gaps: auth coverage, input validation,
  OpenAI error shape, queue/429, streaming headers, secret leakage, user-text XSS, assertions that
  actually assert.

Collect verdicts as BLOCKER/MAJOR/NIT with `file:line` and the violated spec section. Do not fix
anything inline. Create a new task per BLOCKER/MAJOR in the next wave (grouped where they touch the
same files), append the findings to `docs/PROGRESS.md`, and update the task table.
