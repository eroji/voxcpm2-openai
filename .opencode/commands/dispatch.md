---
description: Dispatch every ready task in a wave as parallel workers
agent: lead
---

Dispatch wave $ARGUMENTS (default: next wave in `docs/PLAN.md`).

1. Read `docs/PLAN.md` and `docs/PROGRESS.md`; list the wave's tasks. Verify every `deps:` is
   `pass` and every `owns:` glob is disjoint within the wave. If not, fix the graph first.
2. In **one turn**, call the `subagent` tool once per task with `agent: "worker"` and this prompt
   shape only:
   `Implement task <ID>. Read only your brief at docs/tasks/<ID>.md plus AGENTS.md and
   docs/CONTRACTS.md. You may write only inside: <owns>. Run the brief's verify command until
   green. Append a ## Report with exact command output to your brief. Do not commit, push, tag,
   or run repo-wide formatters. Report BLOCKED instead of editing outside owns:.`
3. When all workers finish, read the full diff (`git diff --stat` + `git diff`) before accepting
   anything. Fix the graph, not worker output, if tasks collided.
4. Run `scripts/verify.sh` once on the integrated tree, then `/review <wave>`.
