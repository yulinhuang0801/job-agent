"""Dice MCP source with a fake MCP server (no network): SSE parsing, session id, 429 backoff, ingest, filters."""
import json, os, sys, tempfile
os.environ["JOB_AGENT_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else str(__import__('pathlib').Path(__file__).resolve().parents[2]))
import httpx
from fastapi.testclient import TestClient
from app import crawlers, db, dice, llm, main, store

llm.chat_json = lambda *a, **k: {"industries": {"Acme Robotics": "Software & Internet"}}
TOOLS = [{"name": "search_jobs", "inputSchema": {"type": "object", "required": ["keyword"], "properties": {
    k: {} for k in ("keyword", "location", "jobs_per_page", "page_number", "workplace_types", "employment_types", "willing_to_sponsor")}}},
    {"name": "get_job_details", "inputSchema": {"properties": {"job_id": {}}}}]
JOBS = [
    {"id": "x1", "guid": "g-1", "title": "Software Engineer Intern", "summary": "Build things. We offer H-1B sponsorship.",
     "postedDate": "2026-10-16T20:09:04Z", "jobLocation": {"displayName": "Austin, Texas, USA"}, "companyName": "Acme Robotics",
     "detailsPageUrl": "https://www.dice.com/job-detail/g-1", "employmentType": "Full-time", "isRemote": False,
     "workplaceTypes": ["On-Site"], "willingToSponsor": True},
    {"id": "x2", "guid": "g-2", "title": "Data Analyst Intern (Summer 2027)", "summary": "Must be a U.S. citizen required.",
     "postedDate": "2026-10-10T00:00:00Z", "jobLocation": {"displayName": ""}, "companyName": "Beta Corp",
     "detailsPageUrl": "https://www.dice.com/job-detail/g-2", "employmentType": "Full-time", "isRemote": True, "willingToSponsor": False},
    {"id": "x3", "guid": "g-3", "title": "Senior Platform Engineer", "summary": "", "jobLocation": {"displayName": "Remote"},
     "companyName": "Gamma", "detailsPageUrl": "https://www.dice.com/job-detail/g-3", "employmentType": "Full-time"},
]
seen = {"calls": [], "sessions": set(), "429": 0}


def sse(obj):
    return httpx.Response(200, headers={"content-type": "text/event-stream", "mcp-session-id": "sess-42"},
                          text=f"event: message\ndata: {json.dumps(obj)}\n\n")


def handler(request):
    body = json.loads(request.content)
    seen["sessions"].add(request.headers.get("mcp-session-id"))
    m = body.get("method")
    if m == "initialize":
        return sse({"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}}})
    if "id" not in body:
        return httpx.Response(202)
    if m == "tools/list":
        return sse({"jsonrpc": "2.0", "id": body["id"], "result": {"tools": TOOLS}})
    if m == "tools/call":
        args = body["params"]["arguments"]
        seen["calls"].append(args)
        if args["keyword"].startswith("QA") and seen["429"] < 1:  # rate limited once, then fine
            seen["429"] += 1
            return httpx.Response(429, headers={"retry-after": "0"})
        if args["keyword"].startswith("broken"):
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"isError": True, "content": [{"type": "text", "text": "bad input"}]}})
        data = JOBS if args["keyword"].startswith("software") else []
        out = {"data": data, "metadata": {"page": 1, "pageSize": 50, "total": len(data)}}
        return sse({"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": json.dumps(out)}], "structuredContent": out, "isError": False}})
    return httpx.Response(400)


fake = httpx.MockTransport(handler)
# keywords from free-text target roles
roles = {"roles": ["Remote unpaid tech internship (exploring software engineering, data/ML, web/frontend, NLP/linguistics-tech, QA, IT/support)"],
         "seniority": "Internship (freshman)", "locations": ["Remote"]}
assert dice.keywords_from_targets(roles) == ["software engineering", "data ML", "web frontend", "NLP linguistics-tech"], dice.keywords_from_targets(roles)
assert dice.keywords_from_targets({"roles": ["Data Analyst", "Quant Research Intern"]}) == ["Data Analyst", "Quant Research"]
assert dice.wants_internships(roles) and not dice.wants_internships({"roles": ["Senior SRE"], "seniority": "Senior"})
store.save("profile", {"targets": roles})
p = dice.plan()
assert p["remote"] and p["interns"] and not p["sponsor"] and len(p["keywords"]) == dice.MAX_SEARCHES
print("ok   search plan from target roles")
# client: session id, SSE parsing, 429 backoff
with dice.Client(transport=fake, pause=0) as c:
    assert set(c.tools()) == {"search_jobs", "get_job_details"}
    assert c.session == "sess-42"
    jobs = dice.search(c, "QA", sponsor=True, remote=True)
    assert seen["429"] == 1 and jobs == []
    assert seen["calls"][-1] == {"keyword": "QA intern", "jobs_per_page": 50, "page_number": 1, "willing_to_sponsor": True, "workplace_types": ["Remote"]}
    try:
        c.call("search_jobs", {"keyword": "broken"})
        assert False
    except dice.DiceError as e:
        assert "bad input" in str(e)
assert "sess-42" in seen["sessions"]
always_429 = httpx.MockTransport(lambda r: httpx.Response(429, headers={"retry-after": "0"}))
try:
    with dice.Client(transport=always_429, pause=0):
        assert False
except dice.DiceError as e:
    assert "limiting requests" in str(e), e
print("ok   MCP client: SSE, session id, isError, 429 backoff and message")
# ingest: internships only, taxonomy, sponsorship mapping; other sources' rows survive
store.save("settings", {**store.get_settings(), "dice_keywords": "software engineering, data", "dice_sponsor": "off"})
with db.connect() as con:
    db.upsert(con, "postings", [{"id": "old", "source": "Company job boards", "board": "lever", "company": "Keep", "domain": "", "title": "Intern",
        "department": "", "function": "", "job_type": "Internship", "term": "", "location": "", "state": "", "country": "", "remote": 0,
        "industry": "", "url": "u", "posted": "", "sponsorship": "", "description": "", "seen": ""}])
n, msg = dice.run(transport=fake, pause=0, progress=lambda m: None)
assert n == 2 and "internships for software engineering, data" in msg, (n, msg)
rows = {r["id"]: r for r in db.search("postings", {"source": "Dice"})["rows"]}
a, b = rows["dice:g-1"], rows["dice:g-2"]
assert a["job_type"] == "Internship" and a["function"] == "Software Engineering" and a["state"] == "TX" and a["sponsorship"] == "Willing to sponsor"
assert a["industry"] == "Software & Internet" and a["posted"] == "2026-10-16" and a["board"] == "dice"
assert b["remote"] == 1 and b["sponsorship"] == "US citizens only" and b["term"] == "Summer 2027"
assert db.search("postings", {"source": "Company job boards"})["total"] == 1
n2, _ = dice.run(transport=fake, pause=0, progress=lambda m: None)  # reruns replace, not duplicate
assert n2 == 2 and db.search("postings", {"source": "Dice"})["total"] == 2
print("ok   Dice rows saved with taxonomy and sponsorship; reruns replace them")
# API: filters, facets, settings saved by refresh, cooldown, status
c = TestClient(main.app)
r = c.get("/api/db/postings", params={"source": "Dice", "job_type": "Internship", "sponsorship": "Willing to sponsor"}).json()
assert r["total"] == 1 and r["rows"][0]["company"] == "Acme Robotics"
assert {f["value"] for f in r["facets"]["source"]} == {"Dice"}
assert c.get("/api/db/postings", params={"source": "Dice", "hide_citizen": "1"}).json()["total"] == 1
assert c.get("/api/db/postings", params={"source": "Dice", "remote": "1"}).json()["total"] == 1
st = c.get("/api/db/status").json()
assert st["dice"]["keywords"] == ["software engineering", "data"]
calls = []
crawlers.run_in_background = lambda name, fn, *args: calls.append(name)
db.set_source("Dice", "done", "x", 2)
res = c.post("/api/db/refresh", json={"source": "dice", "keywords": "data science", "sponsor": True})
assert res.status_code == 400 and "wait" in res.json()["detail"] and not calls
s = store.get_settings(); assert s["dice_keywords"] == "data science" and s["dice_sponsor"] == "on"
with db.connect() as con:
    con.execute("UPDATE sources SET updated = '2020-01-01T00:00:00' WHERE name = 'Dice'")
assert c.post("/api/db/refresh", json={"source": "dice"}).status_code == 200 and calls == ["Dice"]
assert c.get("/api/db/status").json()["dice"]["sponsor"] is True
print("ok   API filters, saved Dice options, cooldown")
print("ALL PASSED")
