import os, sys, tempfile
os.environ["JOB_AGENT_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from fastapi.testclient import TestClient
from app import llm, main, research, services, store

def fake(messages, **kw):
    s = messages[0]["content"]
    if "screen search results" in s:
        return {"candidates": [{"name": "Acme Search Partners", "website": "https://acmesearch.com", "fit_score": 80, "reasons": "searching"},
                               {"name": "Moelis & Company", "website": "https://moelis.com", "fit_score": 70}]}
    if "research a firm" in s:
        return {"summary": "A search fund", "hooks": ["h1"], "people": [{"name": "Jason Wang", "title": "Searcher"}, {"name": "Mary Smith", "title": "Analyst"}],
                "primary_contact": {"name": "Jason Wang", "title": "Searcher"}, "channel": "email"}
    if "cold email" in s:
        return {"subject": "S", "body": "B", "linkedin_note": "N", "followup": "F"}
    if "list every question" in s:
        return {"questions": [{"question": "Why us?", "category": "essay"}, {"question": "Gender", "category": "eeo"}]}
    if "fill in an online job application" in s:
        return {"answers": [{"index": 0, "answer": "Because", "note": ""}, {"index": 1, "answer": "", "note": "Your choice"}]}
    raise AssertionError(s[:60])

llm.chat_json = fake
services.web_search = lambda a: {"results": [{"title": "t", "url": "https://acmesearch.com", "content": "c"}]}
research.crawl_site = lambda w: {"base": w, "domain": "acmesearch.com", "pages": [{"url": w, "text": "team"}],
                                 "emails": ["msmith@acmesearch.com", "info@acmesearch.com"], "ats_links": []}
c = TestClient(main.app)
store.save("settings", {"api_key": "sk-x", "search_key": "tvly-x"})
store.save("profile", {"name": "T"})
r = c.post("/api/find/search", json={"category": "search_funds", "location": "Chicago"}).json()
names = {x["name"]: x for x in r["candidates"]}
assert names["Moelis & Company"]["fit_score"] == 10 and "Large" in names["Moelis & Company"]["red_flags"]
assert all("Chicago" in q for q in r["queries"][:1])
print("ok   search + big-firm filter")
jobs = c.post("/api/find/add", json={"candidates": [names["Acme Search Partners"]]}).json()["jobs"]
again = c.post("/api/find/add", json={"candidates": [names["Acme Search Partners"]]}).json()
assert again["added"] == 0
jid = jobs[0]["id"]
j = c.post(f"/api/jobs/{jid}/research").json()
assert j["contact"]["email"] == "jwang@acmesearch.com" and "pattern" in j["contact"]["email_source"], j["contact"]
print("ok   email pattern guess:", j["contact"]["email"], "|", j["contact"]["email_source"])
j = c.patch(f"/api/jobs/{jid}", json={"contact_email": "jason@acmesearch.com"}).json()
assert j["contact"]["email"] == "jason@acmesearch.com" and j["contact"]["email_source"] == "entered by you"
j = c.post(f"/api/jobs/{jid}/email").json(); assert j["email"]["subject"] == "S"
j = c.patch(f"/api/jobs/{jid}/email", json={"body": "edited"}).json(); assert j["email"]["body"] == "edited"
j = c.post(f"/api/jobs/{jid}/sent").json(); assert j["status"] == "Contacted" and j["followup_due"]
print("ok   email, edit, mark sent (follow up by %s)" % j["followup_due"])
j = c.post(f"/api/jobs/{jid}/form", json={"text": "Why us?\nGender"}).json()
assert j["channel"] == "form" and j["form"]["questions"][1]["answer"] == ""
j = c.patch(f"/api/jobs/{jid}/form", json={"index": 0, "answer": "Mine", "done": True}).json()
assert j["form"]["questions"][0]["done"] and j["form"]["questions"][0]["answer"] == "Mine"
assert c.patch(f"/api/jobs/{jid}/form", json={"index": 9}).status_code == 404
t = c.get(f"/api/jobs/{jid}/form.txt").text; assert "Why us?" in t and "Mine" in t
csv = c.get("/api/jobs.csv").text; assert "jason@acmesearch.com" in csv and "Follow up by" in csv
print("ok   form walkthrough, .txt, csv")
imp = c.post("/api/find/import", json={"text": "Company,Website,First Name,Last Name,Title,Email\nBeta Capital,betacap.com,Ann,Lee,Partner,ann@betacap.com"}).json()
assert imp["candidates"][0]["contact_email"] == "ann@betacap.com"
j = c.post("/api/find/add", json={"candidates": imp["candidates"]}).json()["jobs"][0]
assert j["contact"]["email"] == "ann@betacap.com"
print("ok   CSV import keeps contacts")
print("ALL PASSED")
