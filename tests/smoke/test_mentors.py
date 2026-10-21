import os, sys, tempfile, json
from datetime import date, timedelta
os.environ["JOB_AGENT_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from fastapi.testclient import TestClient
from app import llm, main, store
calls = []
def fake(messages, **kw):
    s = messages[0]["content"]; calls.append(s[:40])
    if "plan for today" in s:
        status = json.loads(messages[1]["content"].split("STATUS:\n", 1)[1])
        assert status["jobs"]["followups_due"][0]["company"] == "Acme"
        return {"headline": "Outreach time", "stage": "Reaching out", "status": "s",
                "tasks": [{"title": "Follow up with Ann at Acme", "why": "due", "minutes": 5, "action": {"type": "job", "job_id": "j1", "label": "Open Acme"}},
                          {"title": "Contact 10 firms", "minutes": 60, "action": {"type": "open", "tab": "find", "label": "Find firms"}},
                          {"title": "Bad action", "action": {"type": "hack", "label": "x"}}], "encouragement": "Go"}
    ctx = json.JSONDecoder().raw_decode(s.split("CONTEXT (today is", 1)[1].split("):\n", 1)[1])[0]
    if "LEAD MENTOR" in s:
        assert "status" in ctx and ctx["status"]["coach_notes"]["behavioral"], ctx["status"]["coach_notes"]
        return {"reply": "Focus on follow-ups.", "actions": [{"type": "coach", "coach": "technical", "label": "Practice", "message": "teach me"},
                                                              {"type": "edit_profile", "label": "no path"}], "progress_note": "ignored"}
    if "BEHAVIORAL COACH" in s:
        assert "answers" in ctx
        return {"reply": "Tell me about a time...", "actions": [{"type": "save_story", "competency": "failure", "label": "Save",
                "fields": {"title": "Lost a game", "result": "We lost by one", "bogus": "x"}}], "progress_note": "Practiced failure story; result vague."}
    if "TECHNICAL COACH" in s:
        assert "skills_check" in ctx and "areas_in_bank" in ctx
        return {"reply": "Let's do valuation.", "actions": [{"type": "skills_check", "areas": ["Valuation (finance technicals)"], "count": 4, "label": "Check"}]}
    if "RESUME COACH" in s:
        assert ctx["profile"]["experience"][0]["bullets"][0] == "Did stuff"
        return {"reply": "Stronger verb.", "actions": [{"type": "edit_profile", "path": "experience.0.bullets.0", "old": "Did stuff", "new": "Built X", "label": "Use"}]}
    raise AssertionError(s[:80])
llm.chat_json = fake
c = TestClient(main.app)
today = date.today().isoformat()
store.save("settings", {"api_key": "sk-x"})
store.save("profile", {"name": "T", "targets": {"roles": ["IB intern"]}, "experience": [{"company": "Acme", "title": "Intern", "bullets": ["Did stuff"]}]})
store.save("jobs", [{"id": "j1", "company": "Acme", "status": "Contacted", "created_at": today + "T09:00:00", "contacted_date": (date.today() - timedelta(days=7)).isoformat(),
                     "followup_due": today, "contact": {"name": "Ann"}, "kind": "firm", "title": "", "url": "", "description": "", "applied_date": "", "notes": ""}])

t = c.get("/api/mentor/today").json()
assert t["plan"]["headline"] == "Outreach time" and len(t["plan"]["tasks"]) == 3
assert t["plan"]["tasks"][0]["action"]["type"] == "job" and t["plan"]["tasks"][2]["action"] is None
assert t["counts"]["followups_due"] == 1 and t["milestones"][1]["done"] and not t["milestones"][0]["done"] is None
n = len(calls); c.get("/api/mentor/today"); assert len(calls) == n, "plan should be cached for the day"
c.get("/api/mentor/today?refresh=true"); assert len(calls) == n + 1
assert c.post("/api/mentor/today/task", json={"index": 1, "done": True}).json()["done"] == [1]
assert c.put("/api/mentor/goal", json={"goal": 15}).json()["goal_per_day"] == 15
assert c.get("/api/mentor/today").json()["goal_per_day"] == 15
print("ok   today plan: cached per day, refresh, tasks, goal, bad actions dropped")

b = c.post("/api/mentor/chat/behavioral", json={"text": "mock interview please"}).json()
act = b["messages"][-1]["actions"][0]; assert act["type"] == "save_story" and "bogus" in act["fields"]
assert c.post("/api/mentor/save-story", json={"competency": act["competency"], "fields": act["fields"]}).status_code == 200
st = c.get("/api/stories").json(); assert st["stories"]["failure"]["status"] == "done" and st["stories"]["failure"]["result"] == "We lost by one"
l = c.post("/api/mentor/chat/lead", json={"text": "what now?"}).json()
assert [a["type"] for a in l["messages"][-1]["actions"]] == ["coach"], l["messages"][-1]["actions"]
tq = c.post("/api/mentor/chat/technical", json={"text": "teach me"}).json()
assert tq["messages"][-1]["actions"][0]["areas"] == ["Valuation (finance technicals)"]
r = c.post("/api/mentor/chat/resume", json={"text": "review"}).json()
a = r["messages"][-1]["actions"][0]
assert c.post("/api/mentor/edit-profile", json=a).status_code == 200
assert store.load("profile", {})["experience"][0]["bullets"][0] == "Built X"
bad = c.post("/api/mentor/edit-profile", json=a); assert bad.status_code == 400 and "changed" in bad.json()["detail"]
assert c.post("/api/mentor/edit-profile", json={"path": "experience.9.bullets.0", "new": "x"}).status_code == 400
print("ok   coaches: each sees its context; save_story, edit_profile (checks old text), lead sees coach notes")
assert len(c.get("/api/mentor/chat/behavioral").json()["messages"]) == 2
assert c.delete("/api/mentor/chat/behavioral").json()["messages"] == []
assert c.post("/api/mentor/chat/nobody", json={"text": "x"}).status_code == 400
print("ALL PASSED")
