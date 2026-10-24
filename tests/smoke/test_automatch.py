"""Daily matches and board discovery with a fake model and no network: the prefilter (level, function,
remote, citizens-only), AI scoring, preparing the top matches in My jobs, add/dismiss, settings limits,
and the discovery helpers (board links, name probes that must name the company, careers-page scan)."""
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

os.environ["JOB_AGENT_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[2]))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import automatch, db, discover, llm, main, official, store  # noqa: E402


def ok(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    assert cond, name


PROFILE = {"name": "Sam Lee", "email": "sam@example.com",
           "targets": {"roles": ["Remote tech internship (software engineering, data/ML)"], "locations": ["Remote"],
                       "seniority": "Internship (freshman)", "work_authorization": "F-1, no CPT yet"},
           "skills": {"technical": [{"name": "Python", "level": "beginner"}]},
           "experience": [], "projects": [{"name": "Bot", "tech": ["Python"]}], "education": [{"school": "U", "end": "2030"}]}
store.save("profile", PROFILE)
today = date.today().isoformat()


def post(pid, title, company, function, job_type="Internship", remote=1, sponsorship="", posted=today, description="Python, APIs " * 40):
    return {"id": pid, "source": "Company job boards", "board": "lever", "company": company, "domain": "jobs.lever.co", "title": title,
            "department": "", "function": function, "job_type": job_type, "term": "Summer 2027", "location": "Remote" if remote else "Austin, TX",
            "state": "", "country": "United States", "remote": remote, "industry": "", "url": f"https://jobs.lever.co/{company.lower()}/{pid}",
            "posted": posted, "sponsorship": sponsorship, "description": description, "seen": today}


with db.connect() as con:
    db.upsert(con, "postings", [
        post("p1", "Software Engineering Intern", "Alpha", "Software Engineering"),
        post("p2", "Machine Learning Intern", "Beta", "Machine Learning & AI"),
        post("p3", "Software Engineer Intern", "Gamma", "Software Engineering", sponsorship="US citizens only"),
        post("p4", "Senior Software Engineer", "Delta", "Software Engineering", job_type="Full-time"),
        post("p5", "Marketing Intern", "Eps", "Marketing"),
        post("p6", "Software Engineering Intern", "Zeta", "Software Engineering", remote=0),
        post("p7", "Data/ML Intern, Platform", "Eta", "Machine Learning & AI"),
    ])

calls = []


def fake(messages, max_tokens=4000, temperature=0.4, **kw):
    s = messages[0]["content"]
    calls.append(s[:25])
    if s.startswith("You pick job postings"):
        items = json.loads(messages[1]["content"].split("POSTINGS:\n", 1)[1])
        # Rated parts (role, requirements, level, logistics); in-field roles count as 4 by rule → Alpha 100, Beta 86, Eta 71, Zeta 50
        dims = {"Alpha": (4, 4, 4, 4), "Beta": (4, 3, 3, 4), "Eta": (2, 2, 3, 3)}
        out = []
        for i in items:
            r, q, l, g = dims.get(i["company"], (1, 1, 2, 1))
            out.append({"id": i["id"], "dims": {"role": r, "requirements": q, "level": l, "logistics": g}, "eligibility": "ok",
                        "why": f"{i['company']} fits", "concerns": ""})
        return {"matches": out}
    if s.startswith("You are a recruiter screening"):
        return {"title": "", "company": "", "dims": {"role": 4, "requirements": {"score": 3, "why": "most"}, "level": 4, "logistics": 4},
                "summary": "ok", "matched": [], "gaps": [],
                "missing_keywords": [], "worth_applying": True, "deadline": "",
                "work_authorization": {"requirement": "none stated", "evidence": "", "ok_for_candidate": None, "note": ""}}
    if s.startswith("You tailor"):
        return {"resume": {"summary": "x"}, "cover_letter": "Dear team"}
    raise AssertionError("unexpected prompt: " + s[:80])


for mod in (llm, automatch, main):
    if hasattr(mod, "llm"):
        mod.llm.chat_json = fake
llm.chat_json = fake
c = TestClient(main.app)

# Prefilter: internships in SWE/ML, remote, no citizens-only; senior, marketing and on-site roles are out
prefs = automatch.preferences(PROFILE)
ok("preferences from the profile", prefs["interns"] and prefs["remote_only"] and prefs["international"]
   and {"Software Engineering", "Machine Learning & AI"} <= set(prefs["functions"]))
rows, stats = automatch.candidates(PROFILE)
ids = {r["id"] for r in rows}
ok("prefilter keeps the right postings", ids == {"p1", "p2", "p6", "p7"})
ok("citizens-only, senior and other-field roles skipped", not ids & {"p3", "p4", "p5"})
ok("too few remote roles: on-site ones included, ranked last", stats["relaxed_remote"] and rows[-1]["id"] == "p6")

# Settings: limits are enforced
s = c.put("/api/settings", json={"automatch_prepare": "2", "automatch_depth": "docs", "automatch_min": "75", "discover_searches": "99"}).json()
ok("settings saved and clamped", s["automatch_prepare"] == "2" and s["discover_searches"] == "30")
c.put("/api/settings", json={"automatch_depth": "everything"})
ok("bad depth refused", store.get_settings()["automatch_depth"] == "docs")

# The run: score, then prepare the top 2 above 75 (Alpha 92, Beta 81) with fit + documents
n, msg = main._match_now()
m = automatch.load()
ok("matches scored", n == 4 and [x["company"] for x in m["items"]] == ["Alpha", "Beta", "Eta", "Zeta"])
jobs = store.load("jobs", [])
ok("top matches prepared in My jobs", {j["company"] for j in jobs} == {"Alpha", "Beta"}
   and all(j["fit"] and j["documents"] and j["status"] == "Ready" and j["auto_match"]["score"] >= 75 for j in jobs))
ok("prepared steps recorded", m["items"][0]["prepared"] == ["fit", "documents"] and m["items"][0]["status"] == "added")
ok("match scores computed from the rubric", [x["score"] for x in m["items"]] == [100, 86, 71, 50] and m["items"][0]["band"]["label"] == "Strong"
   and not m["items"][0]["estimate"])
ok("full fit replaces the estimate on Home", m["items"][0]["fit_score"] == 89 and m["items"][1]["fit_score"] == 89)
ok("below the bar not prepared", m["items"][2]["job_id"] == "" and m["items"][2]["status"] == "new")
ok("mentors see today's strong matches", [x["company"] for x in main.mentors.snapshot()["daily_matches"]["strong"]] == ["Alpha", "Beta"])

# Same day: no second run unless forced; scored postings aren't scored again
ok("one run a day", automatch.run(prepare=main._auto_prepare)[1].startswith("Today's matches are already in"))
calls.clear()
main._match_now()
ok("already-scored postings skipped", not any(x.startswith("You pick") for x in calls))

# Add and dismiss from Home
r = c.post("/api/matches/p7/add").json()
ok("add a match to My jobs (fit only)", r["job_id"] and any(j["id"] == r["job_id"] and j["fit"] and not j["documents"] for j in store.load("jobs", [])))
c.post("/api/matches/p7/dismiss")
ok("dismissed match hidden", "p7" not in {x["id"] for x in c.get("/api/matches").json()["items"]})
ok("unknown match is a 404", c.post("/api/matches/nope/add").status_code == 404)

# Discovery helpers
ok("board names from a company name", discover.slugs("Acme Robotics, Inc.") == ["acmerobotics", "acme-robotics"]
   and discover.slugs("Apple") == [] and "janestreet" in discover.slugs("Jane Street Capital"))
ok("board links found in text", discover._boards_in("Apply: https://jobs.ashbyhq.com/acme/123 or https://nvidia.wd5.myworkdayjobs.com/en-US/Site/job/x")
   == {("ashby", "acme"), ("workday", "nvidia|wd5|Site")})


def api(request):
    u = str(request.url)
    if "boards-api.greenhouse.io/v1/boards/acmerobotics" in u:
        return httpx.Response(200, json={"name": "Acme Robotics"})
    if "boards-api.greenhouse.io/v1/boards/other" in u:
        return httpx.Response(200, json={"name": "A Different Other Co"})
    if "api.lever.co/v0/postings/other" in u:
        return httpx.Response(200, json=[{"descriptionPlain": "Join a team building payroll software."}])
    return httpx.Response(404, json={})


discover.time.sleep = lambda s: None
http = httpx.Client(transport=httpx.MockTransport(api))
ok("name probe finds the company's board", discover.probe("Acme Robotics Inc", http) == ("greenhouse", "acmerobotics"))
ok("same-named board of another company rejected", discover.probe("Other", http) is None)

pages = {"https://firm.com": '<a href="/careers">Careers</a><a href="https://twitter.com/x">x</a>',
         "https://firm.com/careers": '<iframe src="https://job-boards.greenhouse.io/embed/job_board?for=firmco"></iframe><a href="https://jobs.lever.co/firmco">Open roles</a>'}
official.get_html = lambda url: {"url": url, "html": pages[url]} if url in pages else None
ok("careers page scanned for boards", discover.scan_site("firm.com") == {("lever", "firmco")})
print("ALL PASSED")
