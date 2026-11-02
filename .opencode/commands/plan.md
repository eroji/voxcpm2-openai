---
description: Refresh or extend the voxcpm2-openai plan (DAG + briefs) for a milestone
agent: lead
---

Use the `writing-plans` skill to refresh `docs/PLAN.md` for milestone $ARGUMENTS (default: the
next unfinished milestone in `docs/PROGRESS.md`).

Inputs: `SPEC.md`, `ui/**`, `AGENTS.md`, `docs/CONTRACTS.md`, current `docs/PLAN.md`, existing
`docs/tasks/*.md`. Read-only exploration; do not change product code.

Produce:
1. Requirement rows `R-###` with spec citations, added to the inventory (never renumbered).
2. Ambiguities `Q-###` with a chosen default and the human decision still needed, if any.
3. Tasks sized for one worker session (1–3 files, ≤ ~300 changed lines): `id`, milestone, wave,
   `owns:` globs, `deps:`, `verify:` command, requirements covered.
4. Disjointness check: two tasks share a wave only if `owns:` globs are disjoint; shared paths
   become dependency edges.
5. One brief per new task at `docs/tasks/<ID>.md` in the AGENTS.md format.

Report the new/changed task IDs and the next ready wave.
