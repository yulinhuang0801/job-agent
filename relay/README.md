# Shared-key relay

Lets friends use your DeepSeek (and optionally Tavily, Serper, Hunter) keys without ever seeing them, with hard limits for **everyone together**:

| Service | Limit (all invites together) | Why this number |
|---|---|---|
| DeepSeek AI | **¥20 a day** | Your choice. Hard limit: before each call the relay holds the most it could cost and refuses any call that could pass ¥20; afterwards it settles to DeepSeek's real token count. |
| Tavily web search | 25 a day, 900 a month | Free plan: 1,000 a month |
| Serper (Google results) | 30 a day, 2,000 in total | Free credits: 2,500, one time |
| Hunter.io email finder | 40 a month | Free plan: 50 a month |

Days and months are Beijing time (prices and resets follow DeepSeek's clock). Change the numbers in `wrangler.toml` and run `npx wrangler deploy`.

DeepSeek has no per-key spending limit of its own, so this relay is what enforces the ¥20. The limit only covers calls that go through the relay: **any app with your DeepSeek key pasted directly bypasses it**, including your own copy of Job Agent. To make ¥20 the limit for the whole key, give yourself an invite too and remove the key from your own Settings. Keeping only a small balance on DeepSeek is a second safety net.

Tavily, Serper and Hunter stop for free on their own when their free credits run out, as long as you haven't added a card or paid plan there. The limits above spread those credits across the month.

## Cost of running it

Free: a Cloudflare Workers free account (100,000 requests a day) and a D1 database (free tier). Job Agent makes a few dozen requests per student per day.

## Set up (once, about 10 minutes)

```bash
cd relay
./setup.sh
```

It opens Cloudflare in your browser to log in (sign up free if needed), creates the database, deploys the relay, then asks you to paste your keys. They go straight into Cloudflare's encrypted secrets; nothing is printed or saved on your computer except an admin token in `relay/.admin-token` (git ignores it).

## Invite friends

```bash
./invite.sh "Alex Kim"       # prints an invite link for Alex
./invite.sh --usage          # today's budget, and the last 14 days by person
./invite.sh --list           # everyone invited
./invite.sh --off <code>     # turn one invite off
```

Send each friend their own link. Opening it shows a short page with the limits and how to use it; in Job Agent they paste it under **Settings → Shared key**. One invite per person, so you can see who used what and turn one off.

## What passes through it

The text of each AI request and search passes through the relay to DeepSeek, Tavily, Serper or Hunter. The relay stores only counts and cost per invite per day, never the text.

## Test it locally (no keys, no cost)

```bash
cd relay && npm install
# relay/.dev.vars holds fake keys and tiny limits for local testing, for example:
#   DEEPSEEK_KEY=real-deepseek-key  ADMIN_TOKEN=test-admin  DEEPSEEK_BASE=http://127.0.0.1:9911  DAILY_CNY=0.05 …
npx wrangler d1 execute job-agent-relay --local --file schema.sql
../.venv/bin/python ../tests/relay/mock_upstream.py 9911 &
npx wrangler dev --local --port 8787 &
../.venv/bin/python ../tests/relay/test_relay.py http://127.0.0.1:8787 test-admin
```
