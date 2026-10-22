#!/usr/bin/env bash
# Start Job Agent on http://127.0.0.1:8000 and open it in your browser. The first run sets everything up.
# Needs Python 3.9 or newer (https://www.python.org/downloads/). Keep this window open; Ctrl+C stops it.
# Options: PORT=8010 ./run.sh · NO_BROWSER=1 ./run.sh · ./run.sh --setup-only
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  PY=""
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then PY="$c"; break; fi
  done
  if [ -z "$PY" ]; then
    echo "Job Agent needs Python 3.9 or newer. Install it from https://www.python.org/downloads/ and run this again."
    exit 1
  fi
  echo "Setting up Job Agent (the first run takes a minute or two)…"
  "$PY" -m venv .venv
fi
# Install or update the libraries whenever requirements.txt changes (e.g. after downloading a new version).
if ! cmp -s requirements.txt .venv/.installed-requirements; then
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -r requirements.txt
  cp requirements.txt .venv/.installed-requirements
fi
[ "$1" = "--setup-only" ] && exit 0

PORT="${PORT:-8000}"
URL="http://127.0.0.1:$PORT"
if [ -z "$NO_BROWSER" ]; then
  ( for _ in $(seq 1 60); do curl -s -o /dev/null "$URL" && break; sleep 0.5; done
    open "$URL" 2>/dev/null || xdg-open "$URL" 2>/dev/null || true ) &
fi
echo "Job Agent is running at $URL (keep this window open; press Ctrl+C to stop)."
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --reload --reload-dir app
