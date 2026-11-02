#!/usr/bin/env bash
# Local mirror of the GitLab pipeline. Usage: scripts/verify.sh [--fast]
# --fast skips e2e (browser/API end-to-end).
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -n "${PYTHON:-}" ]; then
  PY="$PYTHON"
else
  PY=".venv/bin/python"
  if [ ! -x "$PY" ]; then
    python3 -m venv .venv
    .venv/bin/pip install -q --upgrade pip
    .venv/bin/pip install -q -e '.[dev]'
  fi
fi

FAST=0
[ "${1:-}" = "--fast" ] && FAST=1

step() { printf '\n==> %s\n' "$*"; }

step "format check"
"$PY" -m ruff format --check app tests scripts

step "lint"
"$PY" -m ruff check app tests scripts

step "typecheck"
"$PY" -m mypy app

step "js syntax (node --check)"
if ! command -v node >/dev/null 2>&1; then
  echo "node is required for the JS check (install nodejs, or run the CI pipeline)" >&2
  exit 1
fi
for f in app/static/js/*.js; do node --check "$f"; done

step "unit + integration (coverage)"
"$PY" -m pytest tests --ignore=tests/e2e \
  --cov=app --cov-report=term-missing --cov-report=xml --cov-fail-under=65

if [ "$FAST" -eq 0 ] && [ -d tests/e2e ] && [ -n "$(ls -A tests/e2e 2>/dev/null)" ]; then
  step "e2e"
  "$PY" -m pytest tests/e2e -q
fi

step "python build"
dist="$(mktemp -d)"
trap 'rm -rf "$dist"' EXIT
"$PY" -m build --outdir "$dist" .

step "package contract (Containerfile + s6)"
grep -q 'COPY app/ /opt/app/' deploy/Containerfile
grep -q 'voxcpm2-3090.yaml' deploy/Containerfile
test -d deploy/s6/engine && test -d deploy/s6/app

step "OK"
