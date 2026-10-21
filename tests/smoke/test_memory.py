"""Connected memory: facts flow between agents, coach updates apply, profile merge keeps everything."""
import os, sys, tempfile, json
os.environ["JOB_AGENT_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from fastapi.testclient import TestClient
from app import llm, main, store, memory
seen = {}
def fake(messages, **kw):
    s = messages[0]["content"]; u = messages[-1]["content"] if len(messages) > 1 else ""
    if s.startswith("You are a friendly"):   # intake interview
        seen["interview_system"] = s
        return {"reply": "Noted.", "section": "goals", "progress": 10, "complete": False, "memory": ["Only wants remote roles"]}
    if "LEAD MENTOR" in s and "plan for today" not in s:
        seen["lead"] = s
        return {"reply": "Great news.", "actions": [
                    {"type": "add_profile_item", "section": "experience", "label": "Add Palazzo", "item": {"company": "Palazzo Securities", "title": "Analyst Intern", "start": "Nov 2026", "bullets": ["Built comps"]}},
                    {"type": "update_targets", "targets": {"roles": ["Boutique IB intern"]}, "label": "Update targets"},
                    {"type": "update_job", "job_id": "j1", "status": "Interviewing", "label": "Mark interviewing"}],
                "memory": ["Has a first-round interview at Palazzo Securities on Nov 3"]}
    if "TECHNICAL COACH" in s:
        seen["technical"] = s
        return {"reply": "ok", "actions": [{"type": "update_skill", "name": "Python", "level": "advanced", "evidence": "aced practice", "label": "Update"},
                                          {"type": "add_question", "kind": "technical", "question": "Explain LBO returns", "area": "LBO drills", "rubric": "IRR, MOIC", "label": "Add"}], "memory": []}
    if "update an existing candidate profile" in s:
        seen["merge_input"] = u
        return {"set": {"summary": "AI REWRITE OF SUMMARY", "location": "Chicago, IL", "targets": {"roles": ["AI says tech"], "locations": ["Remote"]}},
                "add": {"experience": [{"company": "Acme", "title": "Intern", "bullets": ["dup"]}, {"company": "NewCo", "title": "Analyst", "bullets": ["New thing"]}],
                        "links": [{"label": "GitHub", "url": "github.com/x"}]},
                "update": [{"section": "experience", "match": "acme", "set": {"title": "AI changed title", "location": "NYC"}, "add_bullets": ["AI bullet"]}],
                "skill_levels": [{"name": "Python", "level": "beginner"}, {"name": "SQL", "level": "intermediate", "verified": True}],
                "evaluation": {"readiness": "fresh"}}
    raise AssertionError(s[:70])
llm.chat_json = fake
c = TestClient(main.app)
store.save("settings", {"api_key": "sk-x"})
store.save("profile", {"name": "T", "summary": "Original summary", "location": "",
    "targets": {"roles": ["Tech intern"]}, "experience": [{"company": "Acme", "title": "Intern", "bullets": ["Did stuff"]}],
    "skills": {"technical": [{"name": "Python", "level": "intermediate"}]}})
store.save("jobs", [{"id": "j1", "company": "Palazzo Securities", "status": "Contacted", "created_at": "2026-10-20T00:00:00", "title": "", "url": "",
                     "description": "", "applied_date": "", "notes": "", "kind": "firm", "contact": {}}])

# 1) intake interview saves a fact; the lead mentor sees it
st = c.post("/api/interview/message", json={"text": "I only want remote"}).json()
assert st["messages"][-1]["remembered"][0]["text"] == "Only wants remote roles"
r = c.post("/api/mentor/chat/lead", json={"text": "I got an interview at Palazzo and they offered me a part-time analyst role starting Nov!"}).json()
assert "Only wants remote roles" in seen["lead"], "lead didn't see the interview fact"
assert r["messages"][-1]["remembered"][0]["text"].startswith("Has a first-round interview")
print("ok   interview fact -> lead mentor; lead saves its own fact")

# 2) the lead's fact reaches the interview coach and the technical coach
c.post("/api/interview/message", json={"text": "more"})
assert "first-round interview at Palazzo" in seen["interview_system"]
c.post("/api/mentor/chat/technical", json={"text": "teach me"})
assert "first-round interview at Palazzo" in seen["technical"]
print("ok   lead's fact -> interview coach and technical coach")

# 3) update buttons apply
acts = r["messages"][-1]["actions"]
for a in acts:
    assert c.post("/api/mentor/apply", json={"action": a}).status_code == 200, a
p = store.load("profile", {})
assert any(e["company"] == "Palazzo Securities" for e in p["experience"]) and p["targets"]["roles"] == ["Boutique IB intern"]
assert store.load("jobs", [])[0]["status"] == "Interviewing"
assert c.post("/api/mentor/apply", json={"action": acts[0]}).status_code == 400  # already there
tacts = c.get("/api/mentor/chat/technical").json()["messages"][-1]["actions"]
for a in tacts:
    assert c.post("/api/mentor/apply", json={"action": a}).status_code == 200
p = store.load("profile", {})
assert p["skills"]["technical"][0]["level"] == "advanced"
assert any(q["area"] == "LBO drills" for q in c.get("/api/questions").json()["technical"])
print("ok   add experience, targets, job status, skill level, question all apply")

# 4) hand edit pins; merge keeps everything and never touches pinned fields
p = store.load("profile", {})
p["summary"] = "My own summary"
c.put("/api/profile", json=p)
pins = memory.pins()
assert "summary" in pins and "targets.roles" in pins and "skills::python" in pins, pins
store.save("interview", {"messages": [{"role": "user", "content": "x"}] * 4, "resume_text": "", "research": []})
r = c.post("/api/profile/build").json()
assert "Only wants remote roles" in seen["merge_input"] and "PROTECTED FIELDS" in seen["merge_input"]
p = store.load("profile", {})
assert p["summary"] == "My own summary", "pinned summary was changed"
assert p["targets"]["roles"] == ["Boutique IB intern"] and p["targets"]["locations"] == ["Remote"], p["targets"]
assert p["location"] == "Chicago, IL"
names = [(e["company"], e["title"]) for e in p["experience"]]
assert names.count(("Acme", "Intern")) == 1 and ("NewCo", "Analyst") in names and ("Palazzo Securities", "Analyst Intern") in names, names
acme = next(e for e in p["experience"] if e["company"] == "Acme")
assert acme["title"] == "Intern" or acme["title"] == "AI changed title"
assert "Did stuff" in acme["bullets"]
py = next(s for s in p["skills"]["technical"] if s["name"] == "Python"); assert py["level"] == "advanced", py
assert any(s["name"] == "SQL" for s in p["skills"]["technical"]) and p["evaluation"]["readiness"] == "fresh"
assert r["_changes"] and any("NewCo" in x for x in r["_changes"])
print("ok   merge: nothing removed, no duplicates, pinned fields untouched, new info added:", len(r["_changes"]), "changes")

ev = [e["text"] for e in c.get("/api/memory").json()["events"]]
assert any("Interviewing" in e for e in ev) and any("Updated the profile" in e for e in ev) and any("Edited the profile by hand" in e for e in ev)
f = c.get("/api/memory").json()["facts"]; fid = f[0]["id"]
assert len(c.delete(f"/api/memory/facts/{fid}").json()["facts"]) == len(f) - 1
assert c.post("/api/memory/facts", json={"text": "Can work 10 h/week until Dec"}).json()["facts"][-1]["source"] == "you"
print("ok   activity log, forget and add facts")
print("ALL PASSED")
