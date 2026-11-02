---
description: Reconcile task status, blockers and next action into docs/PROGRESS.md
agent: lead
---

Recompute status from evidence, not memory:

1. For each task in `docs/PLAN.md`, check whether its brief has a `## Report` and whether its
   `verify:` command passes now. Mark `pass` only with green output; otherwise `todo|wip|blocked`.
2. Regenerate the wave table in `docs/PROGRESS.md`: wave, task, status, verify outcome, owner.
3. Refresh blockers (including external ones such as M0 spikes needing the `vllm` host) and the
   `## Next action` line with the exact command to resume.
4. Append any new decisions to the dated append-only decisions log (never rewrite old entries).
5. Do not run `scripts/verify.sh` for every task here; run it once when the tree claims green.
