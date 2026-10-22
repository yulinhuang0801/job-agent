"""Test the shared-key relay against a local `wrangler dev` with tests/relay/mock_upstream.py (no real keys, no cost).
See relay/README.md → "Test it locally". Usage: .venv/bin/python tests/relay/test_relay.py [http://127.0.0.1:8787] [admin token]
Expects the low limits in relay/.dev.vars: DAILY_CNY=0.05, TAVILY_PER_DAY=3, SERPER_PER_DAY=2, HUNTER_PER_MONTH=1."""
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
ADMIN = {"authorization": f"Bearer {sys.argv[2] if len(sys.argv) > 2 else 'test-admin'}"}
c = httpx.Client(base_url=BASE, timeout=30)


def ok(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    assert cond, name


r = c.post("/admin/codes", json={"name": "Alex"}, headers=ADMIN).json()
code, invite = r["code"], r["invite"]
me = {"authorization": f"Bearer {code}"}
ok("invite created", invite.endswith("/join/" + code))
ok("admin needs the token", c.post("/admin/codes", json={"name": "x"}).status_code == 401)
ok("no code refused", c.post("/v1/chat/completions", json={}).status_code == 401)
ok("wrong code refused", c.get("/status", headers={"authorization": "Bearer nope"}).status_code == 401)
page = c.get(f"/join/{code}")
ok("invite page shows the limits", page.status_code == 200 and "¥0.05" in page.text and "midnight Beijing time" in page.text)

ok("unknown model refused", c.post("/v1/chat/completions", headers=me, json={"model": "gpt-9", "messages": []}).status_code == 400)
calls, last = 0, None
while calls < 50:
    last = c.post("/v1/chat/completions", headers=me, json={"model": "deepseek-flash", "max_tokens": 2000,
                                                             "messages": [{"role": "user", "content": "hi"}]})
    if last.status_code != 200:
        break
    calls += 1
st = c.get("/status", headers=me).json()
ok(f"AI calls stop at the daily limit ({calls} calls)", last.status_code == 402 and "used up" in last.json()["error"]["message"])
per_call = 0.006 if st["peak_prices_now"] else 0.003
ok("spend settled to real token cost, never over the limit", abs(st["ai"]["spent_cny"] - round(calls * per_call, 2)) < 0.011
   and st["ai"]["spent_cny"] <= 0.05)

ok("web searches counted", all(c.post("/tavily/search", headers=me, json={"query": "q", "search_depth": "advanced"}).status_code == 200
                                for _ in range(3)))
ok("web searches stop at the daily limit", c.post("/tavily/search", headers=me, json={"query": "q"}).status_code == 429)
ok("Google searches stop at the daily limit", [c.post("/serper/search", headers=me, json={"q": "q"}).status_code for _ in range(3)] == [200, 200, 429])
ok("email lookups stop at the monthly limit", [c.get("/hunter/email-finder", headers=me, params={"domain": "acme.com", "first_name": "J",
                                                                                               "last_name": "D"}).status_code for _ in range(2)] == [200, 429])
st = c.get("/status", headers=me).json()
ok("status shows every counter", st["web_search"]["today"] == 3 and st["google_search"]["today"] == 2 and st["email_finder"]["this_month"] == 1)
usage = c.get("/admin/usage", headers=ADMIN).json()["last_14_days"]
ok("owner sees usage by person", any(u["name"] == "Alex" and u["service"] == "ai" and u["calls"] == calls for u in usage))
c.delete(f"/admin/codes/{code}", headers=ADMIN)
ok("turned-off invite refused", c.get("/status", headers=me).status_code == 401)
print("ALL PASSED")
