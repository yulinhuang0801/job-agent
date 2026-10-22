"""The demo: demo/make_demo.py copies only the sample student, moves dates, copies AI keys but nothing else of
yours (no inbox login, search keys, SEC email or alert-email postings), and the app runs on it in demo mode."""
import json
import os
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[2])
tmp = Path(tempfile.mkdtemp())
mine, demo = tmp / "data", tmp / "data" / "demo"
mine.mkdir()
os.environ["JOB_AGENT_DATA"] = str(demo)
os.environ["JOB_AGENT_DEMO"] = "1"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "demo"))

import make_demo  # noqa: E402


def ok(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    assert cond, name


(mine / "settings.json").write_text(json.dumps({"api_key": "sk-test-abcdefghijkl", "alert_email": "me@example.com", "alert_password": "secret",
                                                 "search_key": "tvly-x", "serper_key": "s", "hunter_key": "h", "sec_contact": "me@example.com"}))
(mine / "profile.json").write_text(json.dumps({"name": "The Real Student"}))
con = sqlite3.connect(mine / "jobs_db.sqlite")
con.execute("CREATE TABLE postings (id TEXT PRIMARY KEY, board TEXT, title TEXT)")
con.execute("CREATE TABLE sources (name TEXT PRIMARY KEY, status TEXT, message TEXT, rows INTEGER, updated TEXT)")
con.executemany("INSERT INTO postings VALUES (?,?,?)", [("a", "email", "From my LinkedIn alert"), ("b", "lever", "Public posting")])
con.execute("INSERT INTO sources VALUES ('Job alert emails','done','',1,'')")
con.commit()
con.close()

make_demo.main(MINE=mine, DEMO=demo)
s = json.loads((demo / "settings.json").read_text())
ok("AI key copied", s["api_key"] == "sk-test-abcdefghijkl")
ok("nothing else of yours copied", not {"alert_email", "alert_password", "search_key", "serper_key", "hunter_key", "sec_contact"} & set(s))
ok("your profile not copied", json.loads((demo / "profile.json").read_text())["name"] == "Jordan Rivera")
con = sqlite3.connect(demo / "jobs_db.sqlite")
ok("alert-email postings removed", [r[0] for r in con.execute("SELECT id FROM postings")] == ["b"])
con.close()
ok("plan left to be written fresh", "plan" not in json.loads((demo / "mentor.json").read_text()))
built = date.fromisoformat(json.loads((ROOT / "demo" / "seed" / "seed.json").read_text())["built"])
seed_jobs = json.loads((ROOT / "demo" / "seed" / "jobs.json").read_text())
demo_jobs = json.loads((demo / "jobs.json").read_text())
moved = (date.today() - built).days
ev_seed = next(e for j in seed_jobs for e in j.get("events", []) if e["type"] == "interview")
ev_demo = next(e for j in demo_jobs for e in j.get("events", []) if e["type"] == "interview")
ok("dates moved to today", date.fromisoformat(ev_demo["date"]) == date.fromisoformat(ev_seed["date"]) + timedelta(days=moved))

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402

c = TestClient(main.app)
st = c.get("/api/settings").json()
ok("demo mode on, key hint hidden", st["demo"] and st["key_hint"] == "saved")
ok("app reads the sample student", c.get("/api/profile").json()["name"] == "Jordan Rivera")
ok("sample jobs and graded stories", len(c.get("/api/jobs").json()) == 3
   and all(v.get("grade") for v in c.get("/api/stories").json()["stories"].values()))
print("ALL PASSED")
