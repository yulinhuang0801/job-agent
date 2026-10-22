"""Build demo/seed/ from demo/source.json, using the app itself (and your AI key) in a throwaway data folder.

Run once after editing demo/source.json:  .venv/bin/python demo/build_seed.py
The AI grades the stories, checks fit and writes the tailored resume and cover letter for each sample job,
so the demo shows real output. Only the sample student's files are saved; no keys, nothing of yours.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = json.loads((ROOT / "demo" / "source.json").read_text(encoding="utf-8"))
SEED = ROOT / "demo" / "seed"
KEY_FIELDS = ("api_key", "base_url", "model", "backup_key", "backup_base_url", "backup_model")

work = Path(tempfile.mkdtemp(prefix="jobagent-demo-"))
os.environ["JOB_AGENT_DATA"] = str(work)
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app import assess, main, store  # noqa: E402

mine = json.loads((ROOT / "data" / "settings.json").read_text(encoding="utf-8")) if (ROOT / "data" / "settings.json").exists() else {}
if not mine.get("api_key"):
    sys.exit("Save your DeepSeek key in Settings first; the seed's grades and documents are written by the AI.")
store.save("settings", {**store.DEFAULT_SETTINGS, **{k: mine[k] for k in KEY_FIELDS if mine.get(k)}, "automatch": "off", "alert_auto": "off"})
c = TestClient(main.app, raise_server_exceptions=True)
today = date.today()


def day(offset):
    return (today + timedelta(days=int(offset))).isoformat()


def call(method, path, **kw):
    r = getattr(c, method)(path, **kw)
    if r.status_code >= 400:
        sys.exit(f"{method.upper()} {path} failed: {r.status_code} {r.text[:300]}")
    return r.json()


print("Profile, intake chat and outreach details…")
store.save("profile", SOURCE["profile"])
store.save("interview", SOURCE["interview"])
call("put", "/api/outreach", json=SOURCE["outreach"])
for f in SOURCE["facts"]:
    call("post", "/api/memory/facts", json={"text": f})

print("Stories, then rubric grades from the AI…")
state = assess.load_stories()
for comp, fields in SOURCE["stories"].items():
    state["stories"][comp] = {**state["stories"][comp], **fields, "status": "done", "missing": [], "quality": 4}
state["messages"] = [{"role": "assistant", "content": "Let's prepare your behavioral answers. Tell me about yourself.", "competency": "about_me"}]
store.save("stories", state)
call("post", "/api/stories/grade", json={"ids": list(SOURCE["stories"])})

for j in SOURCE["jobs"]:
    print(f"{j['company']}: fit check and documents…")
    job = call("post", "/api/jobs", json={"description": j["description"], "company": j["company"], "title": j["title"], "url": j["url"]})
    call("post", f"/api/jobs/{job['id']}/analyze")
    call("post", f"/api/jobs/{job['id']}/documents")
    patch = {"status": j["status"]}
    if j.get("applied"):
        patch["applied_date"] = day(j["applied"])
    if j.get("deadline"):
        patch["deadline"] = day(j["deadline"])
    call("patch", f"/api/jobs/{job['id']}", json=patch)
    for ev in j.get("events", []):
        done = ev.pop("done", False)
        job = call("post", f"/api/jobs/{job['id']}/events", json={**ev, "date": day(ev["date"])})
        if done:
            call("patch", f"/api/jobs/{job['id']}/events/{job['events'][-1]['id']}", json={"done": True})
    jobs = store.load("jobs", [])
    for x in jobs:
        if x["id"] == job["id"]:
            x["location"] = j["location"]
    store.save("jobs", jobs)

print("Network…")
by_company = {x["company"]: x["id"] for x in store.load("jobs", [])}
for p in SOURCE["network"]:
    net = call("post", "/api/network", json={"name": p["name"], "title": p["title"], "company": p["company"], "how": p["how"],
                                             "status": p["status"], "notes": p["notes"], "next_followup": day(p["followup"]),
                                             "job_id": by_company.get(p["company"], "")})
    pid = next(x["id"] for x in net["people"] if x["name"] == p["name"])
    if p.get("chat"):
        call("post", f"/api/network/{pid}/chat", json={"date": day(p["chat"]), "notes": p["notes"]})

print("Lead mentor's plan for today…")
call("put", "/api/mentor/goal", json={"goal": 5})
call("get", "/api/mentor/today", params={"refresh": "true"})

if SEED.exists():
    shutil.rmtree(SEED)
SEED.mkdir(parents=True)
for name in ("profile", "interview", "outreach", "stories", "jobs", "network", "memory", "mentor"):
    if (work / f"{name}.json").exists():
        shutil.copy(work / f"{name}.json", SEED / f"{name}.json")
(SEED / "seed.json").write_text(json.dumps({"built": today.isoformat()}), encoding="utf-8")
shutil.rmtree(work, ignore_errors=True)
print(f"Done: {SEED}")
