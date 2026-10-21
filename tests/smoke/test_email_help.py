import os, sys, tempfile
os.environ["JOB_AGENT_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from fastapi.testclient import TestClient
from app import main, store
c = TestClient(main.app)
r = c.put("/api/settings", json={"hunter_key": "https://api.hunter.io/v2/email-finder?domain=x.com&first_name=a&last_name=b&api_key=abcdef0123456789abcdef0123456789abcdef01"}).json()
assert store.get_settings()["hunter_key"] == "abcdef0123456789abcdef0123456789abcdef01", store.get_settings()["hunter_key"]
print("ok   pasted Hunter link -> key extracted")
store.save("jobs", [{"id": "j1", "company": "Acme", "website": "https://acme.com", "status": "Saved", "title": "", "url": "", "description": "",
                     "applied_date": "", "notes": "", "created_at": "2026-10-19T00:00:00", "contact": {"name": "Mary Smith", "title": "Partner"}}])
j = c.post("/api/jobs/j1/email-pattern", json={"known_email": "JWang@acme.com", "known_name": "Jason Wang"}).json()
assert j["contact"]["email"] == "msmith@acme.com" and "flast" in j["contact"]["email_source"], j
j = c.post("/api/jobs/j1/email-pattern", json={"known_email": "jason.wang@acme.com", "known_name": "Jason Wang"}).json()
assert j["contact"]["email"] == "mary.smith@acme.com", j["contact"]
r = c.post("/api/jobs/j1/email-pattern", json={"known_email": "info@acme.com", "known_name": "Jason Wang"})
assert r.status_code == 400 and "shared inbox" in r.json()["detail"]
r = c.post("/api/jobs/j1/email-pattern", json={"known_email": "jw@acme.com", "known_name": "Peter Pan"})
assert r.status_code == 400 and "Couldn't match" in r.json()["detail"]
print("ok   pattern builder: msmith@ / mary.smith@, rejects shared inboxes and mismatched names")
print("ALL PASSED")
