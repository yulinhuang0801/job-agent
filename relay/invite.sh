#!/usr/bin/env bash
# Manage invites to your shared-key relay (after ./setup.sh).
#   ./invite.sh "Alex"        make an invite for Alex (prints the link to send)
#   ./invite.sh --list        everyone invited
#   ./invite.sh --off CODE    turn an invite off
#   ./invite.sh --usage       today's budget and the last 14 days by person
set -e
cd "$(dirname "$0")"
URL=$(cat .relay-url 2>/dev/null) || { echo "Run ./setup.sh first."; exit 1; }
TOKEN=$(cat .admin-token 2>/dev/null) || { echo "No admin token: run ./setup.sh first."; exit 1; }
H="authorization: Bearer $TOKEN"
pretty() { python3 -m json.tool; }
case "$1" in
  ""|-h|--help) sed -n 2,6p "$0" ;;
  --list) curl -s -H "$H" "$URL/admin/codes" | pretty ;;
  --off) curl -s -X DELETE -H "$H" "$URL/admin/codes/$2" | pretty ;;
  --usage) curl -s -H "$H" "$URL/admin/usage" | pretty ;;
  *) curl -s -X POST -H "$H" -H "content-type: application/json" -d "{\"name\": $(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1")}" "$URL/admin/codes" | pretty ;;
esac
