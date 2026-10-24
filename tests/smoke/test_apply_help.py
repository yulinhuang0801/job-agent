"""Applying help, with a fake model and no network: exact form questions from Greenhouse/Workable, answers the candidate
types being reused on later forms (never EEO or essays), contact-research confidence labels and source checks, the
mentor side panel's page context, and the Mail-app draft being refused outside your own Mac."""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["JOB_AGENT_DATA"] = tempfile.mkdtemp()
os.environ["APP_PASSWORD"] = "pretend-hosted"  # Mail drafts must be off when the app isn't running on your own Mac
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[2]))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import llm, main, mentors, outreach, research, store  # noqa: E402


def ok(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    assert cond, name


store.save("profile", {"name": "Sam Lee", "email": "sam@example.com", "targets": {"roles": ["SWE intern"]}, "experience": [], "education": []})

# ---- Exact questions from job-board APIs ----
GH = {"title": "SWE Intern", "questions": [
    {"label": "First Name", "required": True, "fields": [{"type": "input_text", "values": []}]},
    {"label": "Can you work 40 hours a week?", "required": True, "fields": [{"type": "multi_value_single_select", "values": [{"label": "Yes"}, {"label": "No"}]}]},
    {"label": "Resume/CV", "required": True, "fields": [{"type": "input_file", "values": []}]}],
    "location_questions": [], "compliance": [{"type": "eeoc", "questions": [{"label": "Gender", "required": False,
                                                                              "fields": [{"type": "multi_value_single_select", "values": [{"label": "Decline"}]}]}]}]}
WK = [{"name": "Details", "fields": [{"label": "Are you authorized to work in the US?", "type": "boolean", "required": True},
                                     {"label": "Education", "type": "group"}]}]


def fake_get(url, *a, **k):
    req = httpx.Request("GET", url)
    if "boards-api.greenhouse.io" in url:
        return httpx.Response(200, json=GH, request=req)
    if "apply.workable.com/api/v1/jobs/" in url:
        return httpx.Response(200, json=WK, request=req)
    return httpx.Response(404, request=req)


outreach.httpx.get = fake_get
gh = outreach._form_text_from_ats("https://job-boards.greenhouse.io/acme/jobs/123")
ok("Greenhouse: exact types and options", "[select field required]" in gh and "options: Yes | No" in gh and "Gender" in gh and "[file field required]" in gh)
wk = outreach._form_text_from_ats("https://apply.workable.com/acme/j/ABC123/")
ok("Workable: yes/no question, groups skipped", "options: Yes | No" in wk and "Education" not in wk)
ok("Ashby and unknown sites fall back to reading the page", outreach._form_text_from_ats("https://jobs.ashbyhq.com/acme/1") == "")

# ---- Answers the candidate types are reused ----
outreach.remember_answer("If you are based in the U.S. please provide your zipcode:", "61820", "personal")
outreach.remember_answer("Gender", "Woman", "eeo")
outreach.remember_answer("Are you Hispanic/Latino?", "No", "other")
outreach.remember_answer("Why us?", "Because...", "essay")
saved = outreach.load_answers()
ok("facts saved; EEO and essays never saved", [a["answer"] for a in saved] == ["61820"])


def fake_llm(messages, **kw):
    s = messages[0]["content"]
    if s.startswith("You read the text of an online job application form"):
        return {"title": "", "questions": [{"question": "If you are based in the U.S. please provide your zipcode:", "type": "text", "category": "personal"},
                                           {"question": "Gender", "type": "select", "category": "eeo", "options": ["Decline"]}]}
    if s.startswith("You help a candidate fill in an online job application"):
        if outreach.load_answers():
            assert "61820" in messages[1]["content"], "earlier answers must reach the model"
        return {"answers": [{"index": 0, "answer": "", "note": ""}, {"index": 1, "answer": "", "note": "Your choice"}]}
    if s.startswith("You work in a job-search program"):
        return {"reply": "Use your ZIP.", "actions": []}
    raise AssertionError(s[:60])


for mod in (llm, outreach, mentors):
    if hasattr(mod, "llm"):
        mod.llm.chat_json = fake_llm
llm.chat_json = fake_llm
c = TestClient(main.app)
j = c.post("/api/jobs", json={"description": "SWE intern at Acme", "company": "Acme", "title": "SWE Intern"}).json()
f = c.post(f"/api/jobs/{j['id']}/form", json={"text": "ZIP code?\nGender?" * 10}).json()["form"]["questions"]
ok("same question on a new form: filled with your earlier answer", f[0]["answer"] == "61820" and "earlier application" in f[0]["note"])
ok("EEO question left for you", f[1]["answer"] == "")
c.patch(f"/api/jobs/{j['id']}/form", json={"index": 1, "answer": "Decline"})
ok("typing an EEO answer doesn't save it", all("gender" not in a["key"] for a in c.get("/api/answers").json()))
c.delete("/api/answers/" + saved[0]["key"])
ok("saved answer can be removed", c.get("/api/answers").json() == [])

# ---- Contact research: confidence labels, LinkedIn only from search results ----
ok("confidence: published High, Hunter Medium, pattern Low, none Not Found",
   [research.email_confidence(e, h) for e, h in [("a@x.com", "published on their website"), ("a@x.com", "found by Hunter.io (confidence 80%)"),
                                                 ("a@x.com", "guessed from the firm's email pattern (flast)"), ("", "")]] == ["High", "Medium", "Low", "Not Found"])
ok("LinkedIn: only real profile URLs", bool(research.LINKEDIN_RE.search("https://www.linkedin.com/in/jane-doe-1/"))
   and not research.LINKEDIN_RE.search("https://www.linkedin.com/search/results/people/?keywords=jane"))
ok("notes drop what the lookups found", research._drop_found("Phone not published; no LinkedIn URL", "https://linkedin.com/in/x", "") == "Phone not published")
r = c.patch(f"/api/jobs/{j['id']}", json={"contact_email": "jane@acme.com"}).json()
ok("an email you type counts as High", r["contact"]["email_confidence"] == "High")

# ---- Mentor side panel: page context reaches the model, label shows in the chat ----
sent = {}
real = fake_llm


def capture(messages, **kw):
    sent["system"] = messages[0]["content"]
    return real(messages, **kw)


mentors.llm.chat_json = capture
c.post(f"/api/jobs/{j['id']}/form", json={"text": "ZIP code?\nGender?" * 10})
chat = c.post("/api/mentor/chat/lead", json={"text": "How should I answer this?", "page": "jobs", "job_id": j["id"], "question_index": 0}).json()
ok("side panel sends the job and the question", "WHERE THE CANDIDATE IS RIGHT NOW" in sent["system"] and "zipcode" in sent["system"])
ok("chat shows what it was about", chat["messages"][-2]["context"].startswith("Acme") and "question 1" in chat["messages"][-2]["context"])
plain = c.post("/api/mentor/chat/lead", json={"text": "hi"}).json()
ok("plain chat has no page context", "WHERE THE CANDIDATE IS RIGHT NOW" not in sent["system"] and "context" not in plain["messages"][-2])

# ---- Mail app drafts only on your own Mac ----
ok("Mail-app drafts off when hosted", c.get("/api/settings").json()["mail_drafts"] is False
   and c.post(f"/api/jobs/{j['id']}/mail-draft", json={"subject": "x", "body": "y"}).status_code == 400)
print("ALL PASSED")
