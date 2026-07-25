#!/usr/bin/env bash
#
# Shreyzen — one-command launcher.
#
# Creates the Python virtualenv if it doesn't exist, then starts the Test Runner.
# Everything else is automatic: pip dependencies install themselves on first
# launch, the Playwright browser auto-installs on the first web/mobile run, and
# the mock API server auto-starts whenever a run needs it.
#
#   ./run.sh                                      # start the UI → http://127.0.0.1:8770
#   ./run.sh serve --port 9100                    # UI on a custom port
#   ./run.sh init --url https://myapp.com         # scaffold onto a new project
#   ./run.sh doctor                               # validate the environment
#   ./run.sh run --scenario crud --profile smoke  # headless load run (CI-style)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV=".venv"
PYBIN="$VENV/bin/python"

# 1. Ensure a virtualenv exists (prefer Python 3.11 — 3.14 is not supported).
if [ ! -x "$PYBIN" ]; then
  PYTHON=""
  for candidate in python3.11 python3.12 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
  done
  if [ -z "$PYTHON" ]; then
    echo "❌ No Python found. Install Python 3.11, then re-run ./run.sh" >&2
    exit 1
  fi
  echo "🐍 Creating virtualenv (.venv) with $PYTHON …"
  "$PYTHON" -m venv "$VENV"
  "$PYBIN" -m pip install --quiet --upgrade pip || true
fi

# 2. Launch the runner. It installs requirements.txt on first run, then serves.
#    Default to `serve` when no subcommand is given.
if [ "$#" -eq 0 ]; then
  set -- serve
fi
exec "$PYBIN" tools/studio.py "$@"
