# Spike scripts (M0, SPEC §12)

These run **on the `vllm` host** against a live VoxCPM2 engine. No engine is
reachable from a development machine (Q-001), so nothing here has been run yet —
see `NOTES.md`. Real requests only: with no engine the scripts exit with
`engine unreachable …` (status 2) and produce no output files.

## Run

```bash
# test container (voxcpm2-test) on the vllm host:
python scripts/spikes/s1_style.py
python scripts/spikes/s2_design.py
python scripts/spikes/s3_stream.py
python scripts/spikes/s4_knobs.py
python scripts/spikes/s5_warmup.py   # run IMMEDIATELY after an engine restart
python scripts/spikes/s6_shim.py     # prints host commands (podman exec); parses --shim-json fixtures

# inside the production container use --base http://127.0.0.1:8102
```

Common flags on every script: `--base`, `--voice`, `--audio`, `--ref-text`, `--text`, `--model`,
`--timeout` (see `--help`). Requires `httpx` (any recent version).

The raw engine has no voice library, so every synthesis request carries `ref_audio` + `ref_text`
(S2 is the exception — description-only is what it tests). `--audio` defaults to the bundled
`app/assets/warmup.wav`, a 1.5 s non-speech tone paired with `--ref-text "Warm-up."` (Q-014): it
proves plumbing only. For a meaningful result point `--audio` at a real 5–10 s cloned-voice clip
and `--ref-text` at its exact transcript.

## Output

Each script prints one JSON line plus an `interpretation:` line, and writes any
audio/fixture files to `scripts/spikes/out/` (gitignored). Paste the JSON line
into the corresponding table row in `NOTES.md`.
