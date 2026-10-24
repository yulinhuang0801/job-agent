"""Core flows with a fake model (no API key needed): settings, About you, profile build and merge,
CV PDF, stories with rubric grades, the skills check (grading, profile sync), custom questions, jobs."""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["JOB_AGENT_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient  # noqa: E402

from app import llm, main, memory, store  # noqa: E402

PROFILE = {
    "name": "Alex Huang", "email": "alex@example.com", "phone": "555-0100", "location": "Champaign, IL",
    "targets": {"roles": ["Software Engineer Intern"], "work_authorization": "F-1 CPT"},
    "summary": "CS student who builds web apps.",
    "education": [{"school": "Example University", "degree": "BS", "field": "Computer Science", "end": "2029"}],
    "experience": [{"company": "Acme", "title": "SWE Intern", "bullets": ["Built an API serving 10k users"]}],
    "projects": [{"name": "Tutor", "tech": ["Python"], "bullets": ["Built a tutor used by 200 students"]}],
    "skills": {"technical": [{"name": "Python", "level": "beginner"}], "tools": ["Git"], "spoken_languages": ["English", "中文"]},
    "evaluation": {"readiness": "Ready", "strengths": ["Python"], "gaps": [], "recommended_roles": [], "next_steps": []},
}
seen = []


def fake(messages, max_tokens=4000, temperature=0.4, **kw):
    s = messages[0]["content"]
    seen.append(s[:30])
    if s.startswith("You are a friendly"):
        return {"reply": "Where do you study?", "section": "education", "progress": 20, "complete": False,
                "memory": ["Wants a remote internship"]}
    if s.startswith("You turn a candidate's"):
        return json.loads(json.dumps(PROFILE))
    if s.startswith("You update an existing candidate profile"):
        return {"add": {"projects": [{"name": "Scraper", "bullets": ["Scraped 5k pages"]}]},
                "update": [{"section": "experience", "match": "acme", "add_bullets": ["Cut latency 40%"]}]}
    if s.startswith("You are an experienced interviewer"):
        return {"reply": "Great. What did you do next?", "fields": {"title": "Fixed the build", "situation": "Our build broke before a demo",
                "action": "I bisected commits and found the bad one", "result": "Demo shipped on time"}, "missing": ["reflection"],
                "quality": 3, "move_on": True}
    if s.startswith("You grade behavioral"):
        items = json.loads(messages[1]["content"])
        return {"grades": [{"id": i["id"], "scores": {"action": {"score": 4, "quote": "I bisected commits", "why": "own steps"},
                                                      "result": {"score": 4, "quote": "made up words", "why": "fake quote"}},
                            "top_fix": "Add a number"} for i in items]}
    if s.startswith("You choose questions"):
        return {"picked": ["py1", "py2", "sql1"], "reason": "Matches your targets."}
    if s.startswith("You grade a short technical"):
        items = json.loads(messages[1]["content"])
        return {"grades": [{"id": i["id"], "score": 3, "feedback": "Good", "model_answer": "..."} for i in items],
                "skills": [{"area": "Python", "level": "intermediate", "evidence": "solid answers"},
                           {"area": "SQL & databases", "level": "beginner", "evidence": "one gap"}], "summary": "Study joins."}
    if s.startswith("You are a recruiter screening"):
        return {"title": "SWE Intern", "company": "Beta", "dims": {"role": 3, "requirements": 3, "level": 4, "logistics": 4},
                "summary": "ok", "matched": [], "gaps": [],
                "missing_keywords": [], "worth_applying": True, "deadline": "2030-01-15",
                "work_authorization": {"requirement": "none stated", "evidence": "", "ok_for_candidate": None, "note": ""}}
    raise AssertionError("unexpected prompt: " + s[:80])


llm.chat_json = fake
for mod in ("assess", "grading", "profile_merge"):
    getattr(__import__("app." + mod, fromlist=["x"]), "llm").chat_json = fake
c = TestClient(main.app)


def ok(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    assert cond, name


# Settings: keys are stored but never returned
r = c.put("/api/settings", json={"api_key": "sk-test-1234567890"}).json()
ok("settings hide the key", r["has_key"] and "api_key" not in r and r["key_hint"].startswith("sk-t"))

# About you
for _ in range(3):
    r = c.post("/api/interview/message", json={"text": "I study CS"}).json()
ok("interview turn", r["messages"][-1]["content"] == "Where do you study?")
ok("interview facts reach shared memory", any("remote internship" in f["text"] for f in memory.facts()))

# Profile: first build, then a merge that never deletes
p = c.post("/api/profile/build").json()
ok("profile built", p["name"] == "Alex Huang")
p = c.post("/api/profile/build").json()
ok("profile merge adds without deleting", any(x["name"] == "Scraper" for x in p["projects"]) and any(x["name"] == "Tutor" for x in p["projects"])
   and "Cut latency 40%" in p["experience"][0]["bullets"])
pdf = c.get("/api/profile/resume.pdf")
ok("CV PDF", pdf.status_code == 200 and pdf.content[:4] == b"%PDF")

# Stories: a turn, then a rubric grade where an invented quote is capped
st = c.post("/api/stories/start").json()
done = []
for _ in range(4):  # the first item may be a short answer; keep going until a STAR story is filled
    st = c.post("/api/stories/message", json={"text": "Our build broke and I bisected commits"}).json()
    done = [k for k, v in st["stories"].items() if v["status"] == "done" and v.get("action")]
    if done:
        break
ok("story finished", done)
g = c.post("/api/stories/grade", json={"ids": done[:1]}).json()["stories"][done[0]]["grade"]
dims = {d["key"]: d for d in g["dims"]}
ok("rubric: real quote keeps its 4", dims["action"]["score"] == 4 and dims["action"]["quote"])
ok("rubric: invented quote capped at 2", dims["result"]["score"] == 2 and not dims["result"]["quote"])
fair = c.post("/api/feedback/grade", json={"kind": "story", "id": done[0], "verdict": "too_harsh", "comment": "x"}).json()
ok("fairness vote saved", fair["stories"][done[0]]["grade"]["verdict"] == "too_harsh"
   and (store.DATA_DIR / "grade_feedback.jsonl").exists())

# Skills check: areas by field, pick, grade, and the profile picks up verified levels
areas = c.get("/api/skills/areas").json()["areas"]
groups = {a.get("group") for a in areas if a["source"] == "Job Agent"}
ok("areas grouped by field", {"Software & data", "Engineering", "Science & health"} <= groups)
sk = c.post("/api/skills/start", json={"count": 3, "areas": []}).json()
ok("skills check started", sk["status"] == "answering" and len(sk["questions"]) == 3 and "rubric" not in sk["questions"][0])
sk = c.post("/api/skills/grade", json={"answers": {q["id"]: "my answer" for q in sk["questions"]}}).json()
ok("skills graded", sk["status"] == "graded" and sk["results"]["total"] == 9)
tech = {x["name"]: x for x in store.load("profile", {})["skills"]["technical"]}
ok("graded levels reach the profile as verified", tech["Python"]["level"] == "intermediate" and tech["Python"]["verified"])

# Custom questions, including the calculation type
q = c.post("/api/questions", json={"kind": "technical", "area": "Lab", "type": "problem", "question": "Dilute 5 M to 0.1 M", "rubric": "C1V1=C2V2"}).json()
ok("custom calculation question", q["technical"][-1]["type"] == "problem")
ok("delete custom question", c.delete(f"/api/questions/{q['technical'][-1]['id']}").json()["technical"] == [])

# Jobs: add by description, score fit (deadline comes along), status change is remembered
j = c.post("/api/jobs", json={"description": "Software engineering intern at Beta. Python required.", "company": "Beta"}).json()
j = c.post(f"/api/jobs/{j['id']}/analyze").json()
ok("fit score computed from the parts, with deadline", j["fit"]["score"] == 89 and j["fit"]["band"]["label"] == "Strong"
   and j["fit"]["verdict"] == "Strong fit" and j["deadline"] == "2030-01-15")
j = c.patch(f"/api/jobs/{j['id']}", json={"status": "Applied"}).json()
ok("status change logged", j["applied_date"] and any("Applied" in e["text"] for e in memory.events(5)))
ok("bad status refused", c.patch(f"/api/jobs/{j['id']}", json={"status": "Nope"}).status_code == 400)
ok("overview badges", c.get("/api/overview").json()["jobs"]["total"] == 1)
print("ALL PASSED")
