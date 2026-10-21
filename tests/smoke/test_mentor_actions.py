"""Mentor buttons that change other sections: a date onto a job and the Calendar, a person into Network,
a status change; and what each coach can see (story grades, upcoming events, jobs index)."""
import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

os.environ["JOB_AGENT_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient  # noqa: E402

from app import llm, main, mentors, pipeline, store  # noqa: E402

d = lambda n: (date.today() + timedelta(days=n)).isoformat()
seen = {}


def fake(messages, **kw):
    s = messages[0]["content"]
    ctx = json.JSONDecoder().raw_decode(s.split("CONTEXT (today is", 1)[1].split("):\n", 1)[1])[0]
    if "LEAD MENTOR" in s:
        seen["lead"] = ctx
        return {"reply": "Added.", "actions": [
            {"type": "add_event", "job_id": "", "company": "Caprae", "event_type": "oa", "date": d(5), "platform": "hirevue", "title": "HireVue", "label": "Add HireVue"},
            {"type": "add_contact", "name": "Sarah Lee", "company": "Houlihan Lokey", "how": "Alumni", "label": "Add Sarah"},
            {"type": "add_event", "company": "Acme", "label": "no date"}], "memory": []}
    if "BEHAVIORAL COACH" in s:
        seen["behavioral"] = ctx
        return {"reply": "Practice.", "actions": []}
    if "TECHNICAL COACH" in s:
        seen["technical"] = ctx
        return {"reply": "Code.", "actions": []}
    raise AssertionError(s[:60])


llm.chat_json = fake
c = TestClient(main.app)


def ok(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    assert cond, name


store.save("settings", {"api_key": "sk-x"})
store.save("profile", {"name": "T", "targets": {"roles": ["Investment banking intern"]}, "education": [{"school": "U", "end": "2029"}]})
store.save("jobs", [{"id": "j1", "company": "Acme", "title": "Analyst", "status": "Applied", "created_at": "2026-10-01T00:00:00",
                     "url": "", "description": "", "applied_date": "", "notes": "", "events": [
                         {"id": "e1", "type": "interview", "title": "Round 1", "date": d(3), "time": "", "notes": "", "platform": "", "people": "", "done": False}]}])
st = {"plan": ["teamwork"], "current": None, "messages": [{"role": "user", "content": "x"}], "sources": [],
      "stories": {"teamwork": {"question": "Teamwork?", "status": "done", "followups": 0, "missing": [], "quality": 2, "title": "t", "situation": "s",
                               "task": "", "action": "we did it", "result": "", "reflection": "", "answer": "",
                               "grade": {"stars": 2, "dims": [{"key": "action", "label": "Your actions", "score": 1, "quote": "", "why": "only we"}],
                                         "top_fix": "Say what you did", "hash": "x", "version": "stories-v1"}}}}
store.save("stories", st)

r = c.post("/api/mentor/chat/lead", json={"text": "Caprae sent a HireVue; I met Sarah"}).json()
acts = r["messages"][-1]["actions"]
ok("undated event dropped, two actions kept", [a["type"] for a in acts] == ["add_event", "add_contact"])
ok("lead sees jobs index and windows", seen["lead"]["status"]["jobs_index"][0]["job_id"] == "j1" and "recruiting_windows" in seen["lead"]["status"])
ok("lead sees weak graded answers", seen["lead"]["status"]["weak_graded_answers"][0]["fix"] == "Say what you did")
for a in acts:
    ok(f"apply {a['type']}", c.post("/api/mentor/apply", json={"action": a}).json()["ok"])
jobs = store.load("jobs", [])
cap = next(j for j in jobs if j["company"] == "Caprae")
ok("new firm tracked and moved to Online assessment", cap["status"] == "Online assessment" and cap["events"][0]["platform"] == "hirevue")
ok("event on the calendar", any(i["company"] == "Caprae" and i["kind"] == "Online assessment" for i in pipeline.calendar(30)))
ok("person in network", pipeline.load_network()[0]["name"] == "Sarah Lee")
r = c.post("/api/mentor/apply", json={"action": {"type": "add_event", "company": "Acme", "event_type": "interview", "date": d(9), "label": "x"}}).json()
ok("event on an existing job by company name", len(next(j for j in store.load("jobs", []) if j["id"] == "j1")["events"]) == 2)

c.post("/api/mentor/chat/behavioral", json={"text": "what first?"})
ok("behavioral coach sees rubric and upcoming interview", seen["behavioral"]["answers"]["Teamwork or leadership"]["rubric"]["biggest_fix"] == "Say what you did"
   and any(i["kind"] == "Interview" for i in seen["behavioral"]["upcoming"]))
c.post("/api/mentor/chat/technical", json={"text": "what first?"})
ok("technical coach sees upcoming assessment", any(i["kind"] == "Online assessment" for i in seen["technical"]["upcoming"]))
ok("update_job accepts new statuses", c.post("/api/mentor/apply", json={"action": {"type": "update_job", "job_id": "j1", "status": "Accepted", "label": "x"}}).json()["ok"])
ok("an accepted job leaves the calendar", not any(i["job_id"] == "j1" for i in pipeline.calendar(30)))
print("ALL PASSED")
