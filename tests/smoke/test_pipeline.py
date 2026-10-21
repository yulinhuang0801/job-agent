"""Pipeline routes with a fake model: events, calendar, .ics, offers, network, timeline, practice."""
import os, sys, tempfile
from datetime import date, timedelta
os.environ["JOB_AGENT_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from fastapi.testclient import TestClient
from app import llm, main, store

def fake(messages, max_tokens=4000, temperature=0.4, **kw):
    s = messages[0]["content"]
    if s.startswith("You prepare a college student for one upcoming"):
        return {"summary": "HireVue prep", "steps": [{"title": "Mock", "why": "", "minutes": 20, "action": {"type": "coach", "coach": "behavioral", "label": "Mock", "message": "go"}}],
                "practice_questions": [{"question": "Tell me about yourself.", "kind": "behavioral", "why": ""}]}
    if s.startswith("You draft one short"):
        return {"subject": "Thanks", "body": "Thank you, Jake.", "linkedin_note": "Hi"}
    if s.startswith("You prepare a college student for a 15-20"):
        return {"intro": "I'm a freshman.", "questions": ["What do you do?"], "avoid": ["Asking for a job"]}
    if s.startswith("You grade behavioral"):
        return {"grades": [{"id": "p", "scores": {"relevance": {"score": 4, "quote": "I am a freshman", "why": "ok"}}, "top_fix": "close"}]}
    return {}
llm.chat_json = fake
for m in ("pipeline", "grading", "assess"):
    getattr(__import__("app." + m, fromlist=["x"]), "llm").chat_json = fake
store.save("profile", {"name": "A", "education": [{"school": "University of Illinois", "end": "2030"}], "targets": {"roles": ["Investment banking intern"]}})
store.save("jobs", [{"id": "j1", "company": "Acme", "title": "Intern", "status": "Applied", "created_at": "2026-10-01T00:00:00", "url": "", "description": "", "applied_date": "", "notes": ""},
                    {"id": "j2", "company": "Beta", "title": "Analyst", "status": "Saved", "created_at": "2026-10-01T00:00:00", "url": "", "description": "", "applied_date": "", "notes": ""}])
c = TestClient(main.app)
d = lambda n: (date.today() + timedelta(days=n)).isoformat()
def ok(name, cond):
    print(("ok   " if cond else "FAIL ") + name); assert cond, name

j = c.post("/api/jobs/j1/events", json={"type": "oa", "platform": "hirevue", "date": d(3)}).json()
ok("OA advances status", j["status"] == "Online assessment" and j["events"][0]["platform"] == "hirevue")
j = c.post("/api/jobs/j1/events", json={"type": "interview", "title": "Round 1", "date": d(-1)}).json()
ok("interview advances status", j["status"] == "Interviewing")
ok("bad date rejected", c.post("/api/jobs/j1/events", json={"type": "oa", "date": "soon"}).status_code == 400)
ok("deadline saved", c.patch("/api/jobs/j2", json={"deadline": d(10)}).json()["deadline"] == d(10))
oa, iv = j["events"][0]["id"], j["events"][1]["id"]
ok("prep", c.post(f"/api/jobs/j1/events/{oa}/prep").json()["events"][0]["prep"]["summary"] == "HireVue prep")
t = c.post(f"/api/jobs/j1/events/{iv}/thanks", json={"notes": "deal flow", "people": "Jake"}).json()["events"][1]
ok("thank-you draft", t["thank_you"]["body"].startswith("Thank you") and t["people"] == "Jake")
cal = c.get("/api/calendar").json()
kinds = [i["kind"] for i in cal["items"]]
ok("calendar has thank-you, OA and deadline", "Thank-you note" in kinds and "Online assessment" in kinds and "Application deadline" in kinds)
ok("timeline knows summers", cal["timeline"]["grad_year"] == 2030 and cal["timeline"]["fields"][0]["yours"])
c.patch(f"/api/jobs/j1/events/{iv}", json={"thanks_sent": True})
ok("thank-you gone once sent", "Thank-you note" not in [i["kind"] for i in c.get("/api/calendar").json()["items"]])
ics = c.get("/api/calendar.ics")
ok("ics", ics.status_code == 200 and ics.text.startswith("BEGIN:VCALENDAR") and "END:VCALENDAR" in ics.text)
o = c.put("/api/jobs/j2/offer", json={"pay": "30", "pay_unit": "hour", "deadline": d(5)}).json()
ok("offer advances status", o["status"] == "Offer" and o["offer"]["pay"] == "30")
ok("offer draft", c.post("/api/jobs/j2/offer/draft", json={"kind": "extension"}).json()["offer"]["draft"]["body"])
ok("offer deadline on calendar", any(i["kind"] == "Offer deadline" for i in c.get("/api/calendar").json()["items"]))
n = c.post("/api/network", json={"name": "Priya", "company": "HL", "how": "Alumni"}).json()
pid = n["people"][0]["id"]
ok("network school from profile", n["school"] == "University of Illinois")
ok("network draft", c.post(f"/api/network/{pid}/draft", json={"kind": "coffee_chat"}).json()["people"][0]["draft"]["linkedin_note"] == "Hi")
ok("chat prep", c.post(f"/api/network/{pid}/prep").json()["people"][0]["prep"]["questions"])
p = c.post(f"/api/network/{pid}/chat", json={"notes": "advice"}).json()["people"][0]
ok("log chat sets thank-you follow-up", p["status"] == "Chatted" and p["next_followup"] == d(1))
ok("messaged sets 7-day follow-up", c.patch(f"/api/network/{pid}", json={"status": "Messaged", "next_followup": ""}).json()["people"][0]["next_followup"] == d(7))
ov = c.get("/api/overview").json()
ok("overview has network and calendar", ov["network"]["total"] == 1 and "week" in ov["calendar"])
g = c.post("/api/practice/grade", json={"question": "Tell me about yourself.", "answer": "I am a freshman at Illinois studying CS."}).json()
ok("practice grade (unverified 4 capped? no: quote exists)", g["dims"][0]["score"] == 4 and 1 <= g["stars"] <= 5)
ok("practice needs an answer", c.post("/api/practice/grade", json={"question": "x", "answer": "hi"}).status_code == 400)
ok("delete event", len(c.delete(f"/api/jobs/j1/events/{oa}").json()["events"]) == 1)
ok("statuses", "Online assessment" in c.get("/api/meta").json()["statuses"])
from app import taxonomy
ok("citizen-only hint", taxonomy.sponsorship_hint("Must be a U.S. citizen") == "US citizens only")
print("ALL PASSED")
