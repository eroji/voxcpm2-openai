#!/usr/bin/env bash
# Smoke test a running voxcpm2-openai deployment.
# Usage: BASE_URL=http://127.0.0.1:8881 [API_KEY=*** VOICE=ashley-en] scripts/smoke.sh
#   API_KEY      bearer token for /v1 when API_KEYS is set on the server (never echoed)
#   STUDIO_AUTH  user:pass basic auth for /api when STUDIO_AUTH is set on the server (never echoed)
#   VOICE        voice name to exercise (default ashley-en)
# Exit 0 when every check passes, 1 otherwise. curl + python3 only, no jq.
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8881}"
VOICE="${VOICE:-ashley-en}"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

pass=0
fail=0
check() { # check <label> <function>
  local label=$1 fn=$2
  if "$fn"; then
    printf 'PASS  %s\n' "$label"
    pass=$((pass + 1))
  else
    printf 'FAIL  %s\n' "$label"
    fail=$((fail + 1))
  fi
}

# curl wrappers; auth values are passed as args only, never printed.
v1() {
  if [ -n "${API_KEY:-}" ]; then
    curl "$@" -H "Authorization: Bearer $API_KEY"
  else
    curl "$@"
  fi
}
api() {
  if [ -n "${STUDIO_AUTH:-}" ]; then
    curl "$@" -u "$STUDIO_AUTH"
  else
    curl "$@"
  fi
}
status_of() { curl -s -o /dev/null -w '%{http_code}' "$BASE_URL$1"; }

c_health() { [ "$(status_of /health)" = 200 ]; }

c_ready() { [ "$(status_of /ready)" = 200 ]; }

c_models() {
  v1 -s "$BASE_URL/v1/models" | python3 -c '
import json, sys
d = json.load(sys.stdin)
sys.exit(0 if any(m.get("id") == "voxcpm2" for m in d.get("data", [])) else 1)' 2>/dev/null
}

c_voices() {
  v1 -s "$BASE_URL/v1/audio/voices" | python3 -c '
import json, sys
d = json.load(sys.stdin)
sys.exit(0 if any(v.get("name") == sys.argv[1] for v in d.get("voices", [])) else 1)' "$VOICE" 2>/dev/null
}

c_speech_wav() {
  v1 -s -D "$tmp/headers" -o "$tmp/speech.wav" -H 'Content-Type: application/json' \
    -d "{\"model\":\"tts-1\",\"voice\":\"$VOICE\",\"input\":\"Smoke test one two three.\",\"response_format\":\"wav\"}" \
    "$BASE_URL/v1/audio/speech" || return 1
  head -c 4 "$tmp/speech.wav" | grep -q RIFF || return 1
  grep -qi '^x-sample-rate:[[:space:]]*48000' "$tmp/headers"
}

c_stream_pcm() {
  # -m 2 aborts the stream after 2 s; bytes in the file then prove the first
  # chunk arrived within the budget. curl exits 28 on that timeout, which is fine.
  local c
  c=$(v1 -s -m 2 -o "$tmp/stream.pcm" -w '%{http_code}' -H 'Content-Type: application/json' \
    -d "{\"model\":\"tts-1\",\"voice\":\"$VOICE\",\"input\":\"Streaming smoke test.\",\"response_format\":\"pcm\",\"stream\":true}" \
    "$BASE_URL/v1/audio/speech" 2>/dev/null) || true
  [ "$c" = 200 ] && [ -s "$tmp/stream.pcm" ]
}

c_engine() {
  api -s "$BASE_URL/api/engine" | python3 -c '
import json, sys
sys.exit(0 if json.load(sys.stdin).get("ready") is True else 1)' 2>/dev/null
}

check "/health returns 200" c_health
check "/ready returns 200 (warm-up done)" c_ready
check "/v1/models lists voxcpm2" c_models
check "/v1/audio/voices lists $VOICE" c_voices
check "WAV speech: RIFF magic + X-Sample-Rate: 48000" c_speech_wav
check "streamed PCM bytes within 2 s" c_stream_pcm
check "/api/engine reports ready:true" c_engine

printf -- '----\n'
printf 'smoke: %d passed, %d failed (%s, voice %s)\n' "$pass" "$fail" "$BASE_URL" "$VOICE"
[ "$fail" -eq 0 ]
