#!/usr/bin/env bash
# Run every smoke test with a fake model (no API key, no network calls for AI). Usage: bash tests/run_smoke.sh
cd "$(dirname "$0")/.." || exit 1
PY=".venv/bin/python"; [ -x "$PY" ] || PY=python3
fail=0
for t in tests/smoke/test_*.py; do
  out=$("$PY" "$t" 2>&1); last=$(printf "%s" "$out" | tail -1)
  if [ "$last" = "ALL PASSED" ]; then echo "pass  $t"; else echo "FAIL  $t"; printf "%s\n" "$out" | tail -8; fail=1; fi
done
exit $fail
