#!/usr/bin/env bash
# One-time setup of the shared-key relay on your free Cloudflare account. Run it yourself in Terminal:
#   cd "relay" && ./setup.sh
# It opens Cloudflare in your browser to log in (or sign up, free), creates the database, deploys the relay, and asks
# you to paste your keys. Keys go straight to Cloudflare's encrypted secrets; they aren't printed or saved here.
set -e
cd "$(dirname "$0")"
export PATH="/usr/local/bin:$PATH"
command -v npx >/dev/null || { echo "Needs Node.js (https://nodejs.org)."; exit 1; }
[ -x node_modules/.bin/wrangler ] || npm install --no-audit --no-fund
W="npx wrangler"

echo "1/5  Log in to Cloudflare (a browser window opens; sign up for free if you don't have an account)…"
$W whoami 2>/dev/null | grep -q "associated with the email" || $W login

if grep -q REPLACE_WITH_ID wrangler.toml; then
  echo "2/5  Creating the database…"
  out=$($W d1 create job-agent-relay 2>&1) || { echo "$out"; exit 1; }
  id=$(printf '%s' "$out" | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1)
  [ -n "$id" ] || { echo "$out"; echo "Couldn't read the database id. Paste it into wrangler.toml (database_id) and run this again."; exit 1; }
  sed -i.bak "s/REPLACE_WITH_ID_FROM_wrangler_d1_create/$id/" wrangler.toml && rm -f wrangler.toml.bak
else
  echo "2/5  Database already set up."
fi
$W d1 execute job-agent-relay --remote --file schema.sql >/dev/null

echo "3/5  Deploying the relay…"
url=$($W deploy 2>&1 | tee /dev/stderr | grep -oE 'https://[a-z0-9.-]+\.workers\.dev' | head -1)
[ -n "$url" ] && echo "$url" > .relay-url

echo "4/5  Your keys (paste each and press Enter; Enter alone skips an optional one). Nothing is shown as you paste."
put() { # name, label, required
  local v=""
  while :; do
    read -r -s -p "   $2: " v; echo
    [ -n "$v" ] || [ "$3" != "required" ] && break
    echo "   This one is required."
  done
  [ -n "$v" ] && printf '%s' "$v" | $W secret put "$1" >/dev/null && echo "   ✓ $1 saved"
  return 0
}
put DEEPSEEK_KEY "DeepSeek API key (required)" required
put TAVILY_KEY "Tavily key (optional, web search)"
put SERPER_KEY "Serper key (optional, Google results)"
put HUNTER_KEY "Hunter.io key (optional, email finder)"
if ! grep -q dashboard-password-set .admin-token.meta 2>/dev/null; then
  while :; do
    read -r -s -p "   Choose a password for your owner dashboard (12+ characters): " pw; echo
    [ ${#pw} -ge 12 ] && break
    echo "   Use at least 12 characters."
  done
  printf '%s' "$pw" | $W secret put DASHBOARD_PASSWORD >/dev/null && echo "   ✓ Dashboard password saved" && echo dashboard-password-set > .admin-token.meta
  unset pw
fi
if [ ! -s .admin-token ]; then
  (umask 077; openssl rand -hex 24 > .admin-token)
  $W secret put ADMIN_TOKEN < .admin-token >/dev/null
  echo "   ✓ Admin token created (saved in relay/.admin-token on this computer only)"
fi

echo "5/5  Done. Relay: $(cat .relay-url 2>/dev/null || echo '(see the deploy output above)')"
echo "Your private dashboard (bookmark it): $(cat .relay-url 2>/dev/null)/dashboard"
echo "Make invites there, or here:  ./invite.sh \"Their name\""
