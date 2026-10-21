import os, sys, tempfile
os.environ["JOB_AGENT_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from fastapi.testclient import TestClient
from app import crawlers, db, main, store, taxonomy as T
# rules
assert T.classify_job_type("Software Engineer Intern (Summer 2027)") == "Internship"
assert T.classify_job_type("Data Analyst Co-op") == "Co-op"
assert T.classify_job_type("Senior Engineer", "Full-time") == "Full-time"
assert T.classify_job_type("New Grad Software Engineer") == "New grad / Entry level"
assert T.classify_function("Quant Research Intern") == "Quantitative Finance"
assert T.classify_function("Data Scientist Intern") == "Data Science & Analytics"
assert T.classify_function("Machine Learning Engineer") == "Machine Learning & AI"
assert T.classify_function("Backend Software Engineer") == "Software Engineering"
assert T.classify_function("Investment Banking Summer Analyst") == "Investment Banking & Finance"
assert T.classify_function("Research Associate, Biology") == "Science & Research"
assert T.parse_location("Austin, TX") == ("TX", "United States", False)
assert T.parse_location("Remote (United States | Canada)")[2] is True
assert T.parse_location("New York City")[0] == "NY" and T.parse_location("Munich, Germany")[1] == "Germany"
assert T.sponsorship_hint("We are unable to provide visa sponsorship for this role") == "No sponsorship"
assert T.classify_term("Data Intern - Winter 2027") == "Winter 2027"
assert crawlers._title("DV CAPITAL ADVISORS, LLC") == "Dv Capital Advisors, LLC"
assert crawlers.board_from_url("https://jobs.lever.co/acme/123") == ("lever", "acme")
assert crawlers.board_from_url("https://job-boards.greenhouse.io/stripe/jobs/1") == ("greenhouse", "stripe")
assert crawlers.board_from_url("https://example.com") == (None, None)
print("ok   classification and parsing rules")
# database search
with db.connect() as con:
    db.upsert(con, "firms", [
        {"id": "a1", "source": "SEC Form ADV", "name": "Small PE", "legal_name": "", "kind": "adviser", "industry": "Private Equity", "category": "Private equity",
         "city": "Chicago", "state": "IL", "country": "United States", "website": "https://smallpe.com", "domain": "smallpe.com", "phone": "", "employees": 8,
         "size": "1-10", "assets": 50_000_000, "small": 1, "fund_types": "Private equity", "filed": "2026-09-01", "founded": "", "raised": None, "extra": {}},
        {"id": "a2", "source": "SEC Form ADV", "name": "Huge Hedge", "legal_name": "", "kind": "adviser", "industry": "Hedge Funds & Asset Management", "category": "Hedge fund",
         "city": "New York", "state": "NY", "country": "United States", "website": "", "domain": "", "phone": "", "employees": 900,
         "size": "500+", "assets": None, "small": 0, "fund_types": "Hedge fund", "filed": "2026-08-01", "founded": "", "raised": None, "extra": {}}])
    con.execute("INSERT INTO people VALUES ('a1','Ann Lee','Managing Partner','SEC Form D')")
    db.upsert(con, "postings", [{"id": "p1", "source": "Company job boards", "board": "lever", "company": "Acme", "domain": "jobs.lever.co", "title": "Data Analyst Intern",
        "department": "", "function": "Data Science & Analytics", "job_type": "Internship", "term": "Summer 2027", "location": "Remote", "state": "", "country": "United States",
        "remote": 1, "industry": "Fintech", "url": "https://jobs.lever.co/acme/1", "posted": "2026-10-01", "sponsorship": "", "description": "", "seen": ""}])
c = TestClient(main.app)
store.save("settings", {"api_key": "sk-x"})
r = c.get("/api/db/firms", params={"small": "1", "state": "IL"}).json()
assert r["total"] == 1 and r["rows"][0]["people"] == [{"name": "Ann Lee", "title": "Managing Partner"}]
assert c.get("/api/db/firms", params={"q": "huge"}).json()["total"] == 1
assert c.get("/api/db/firms", params={"has_website": "1"}).json()["total"] == 1
f = c.get("/api/db/firms").json()["facets"]; assert {x["value"] for x in f["industry"]} == {"Private Equity", "Hedge Funds & Asset Management"}
p = c.get("/api/db/postings", params={"job_type": "Internship", "remote": "1"}).json(); assert p["total"] == 1 and p["facets"]["remote"][0]["count"] == 1
st = c.get("/api/db/status").json(); assert st["counts"]["firms"] == 2 and not st["sec_contact_set"]
assert c.post("/api/db/refresh", json={"source": "adv"}).status_code == 400  # no SEC contact
assert c.post("/api/db/refresh", json={"source": "nope"}).status_code == 400
print("ok   database search, facets, people, status and refresh guards")
print("ALL PASSED")
