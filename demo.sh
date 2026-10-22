#!/usr/bin/env bash
# Start the demo on http://127.0.0.1:8001: Jordan Rivera, a made-up sample student, so you can show Job Agent
# without showing your own profile, stories or jobs. Your data folder isn't touched, and the demo starts
# fresh every time. It can run next to your own copy (port 8000). Ctrl+C stops it.
set -e
cd "$(dirname "$0")"
bash run.sh --setup-only
.venv/bin/python demo/make_demo.py

PORT="${PORT:-8001}"
URL="http://127.0.0.1:$PORT"
JOB_AGENT_DATA="$PWD/data/demo" JOB_AGENT_DEMO=1 .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --log-level warning &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT INT TERM
for _ in $(seq 1 60); do curl -s -o /dev/null "$URL/api/settings" && break; sleep 0.5; done
# Today's matches for the sample student (real postings, scored by the AI; a minute or two), then the lead
# mentor's plan, which includes them. Both run in the background; the page fills in as they finish.
curl -s -X POST "$URL/api/matches/run" -H 'Content-Type: application/json' -d '{"full": false}' >/dev/null || true
( for _ in $(seq 1 120); do curl -s "$URL/api/matches" | grep -q '"running":false' && break; sleep 3; done
  curl -s "$URL/api/mentor/today" >/dev/null ) &
[ -n "$NO_BROWSER" ] || open "$URL" 2>/dev/null || xdg-open "$URL" 2>/dev/null || true
echo "Demo running at $URL (press Ctrl+C to stop)."
wait $SERVER
