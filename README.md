# voxcpm2-openai

VoxCPM2 text-to-speech behind an OpenAI-compatible `/v1/audio/speech` API, plus the
Voice Studio UI. See `SPEC.md` (authoritative) and `docs/PLAN.md` (implementation plan).

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
scripts/verify.sh            # format, lint, types, tests, coverage, e2e
.venv/bin/uvicorn app.main:app --port 8000   # needs an engine on :8102 for real synthesis
```

Local dev without a GPU engine: `tests/fake_engine.py` serves the same API shape.

- Operating contract for agents: `AGENTS.md`
- Contracts: `docs/CONTRACTS.md`
- Status: `docs/PROGRESS.md` · Milestones: `docs/MILESTONES.md`
- Deploy/cutover: `docs/RUNBOOK.md`
