"""Shared key (invite to relay/worker.js) without a running relay: invite parsing and checks, which provider
each service uses (your own key first), relay error messages, and the smaller search budget on a shared key.
The relay itself is tested by tests/relay/test_relay.py against a local `wrangler dev`."""
import os
import sys
import tempfile
from pathlib import Path

os.environ["JOB_AGENT_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient  # noqa: E402

from app import discover, llm, main, services, shared, store  # noqa: E402


def ok(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    assert cond, name


INVITE = "https://job-agent-relay.example.workers.dev/join/abc123xyz789"
ok("invite parsed", shared.parse(INVITE) == ("https://job-agent-relay.example.workers.dev", "abc123xyz789"))
ok("other links refused", shared.parse("https://evil.example/abc") is None and shared.parse("http://example.com/join/abc123xyz789") is None)

c = TestClient(main.app)
ok("bad invite refused with a hint", c.put("/api/settings", json={"relay_invite": "hello"}).status_code == 400)
st = c.put("/api/settings", json={"relay_invite": INVITE + "/"}).json()
ok("invite saved, hidden, counts as an AI key", st["has_relay"] and st["has_ai"] and not st["has_key"]
   and "relay_invite" not in st and INVITE not in str(st) and st["relay_host"] == "job-agent-relay.example.workers.dev")

p = llm._providers()[0]
ok("AI goes through the relay with the cheap model", p["name"] == "Shared key" and p["base_url"].endswith("workers.dev/v1")
   and p["key"] == "abc123xyz789" and p["model"] == "deepseek-flash" and p["deepseek"])
ok("search, Google and email finder on", services.search_enabled() and services.serper_enabled() and services.hunter_enabled())

sent = []
shared.post = lambda path, payload, timeout=30: sent.append((path, payload)) or {"results": [{"title": "t", "url": "u", "content": "c"}], "organic": []}
services.web_search({"query": "acme interview"})
services.serper_search({"query": "acme"})
ok("searches go to the relay", [x[0] for x in sent] == ["/tavily/search", "/serper/search"])


class FakeErr:
    body = {"error": {"message": "The shared AI budget for today is used up (¥20.00 of ¥20).", "type": "budget_exhausted"}}


ok("relay's own message shown", llm._relay_message(FakeErr()).startswith("The shared AI budget for today is used up"))

calls = []
discover.from_search = lambda budget, profile=None: calls.append(budget) or (set(), 0)
s = store.get_settings()
s["discover_searches"] = "8"
store.save("settings", s)
discover.probe_names = lambda *a, **k: ({}, 0)
discover.from_hacker_news = lambda: set()
discover.run()
ok("only 2 board searches a day on a shared key", calls == [2])

c.put("/api/settings", json={"api_key": "sk-own-key-1234567890"})
ok("your own key is used first", llm._providers()[0]["name"] == "DeepSeek")
c.delete("/api/settings/relay_invite")
ok("invite removed", not c.get("/api/settings").json()["has_relay"])
print("ALL PASSED")
