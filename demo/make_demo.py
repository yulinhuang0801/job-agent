"""Set up a fresh demo in data/demo: the sample student from demo/seed/, with every date moved so the seed's
"today" is today (interviews stay "in 3 days", follow-ups stay due). Your own data folder is never touched.

- AI keys (DeepSeek and the backup only) are copied from your Settings so the demo's chat and grading work.
  Nothing else of yours is copied: no profile, stories, jobs, inbox login, search keys or SEC email.
- The firm and job database is copied without the postings from your own job-alert emails.
- Daily matches run on the demo student (2 prepared, no paid web searches).
Run by demo.sh; safe to run again (it starts over).
"""
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "demo" / "seed"
MINE = ROOT / "data"
DEMO = MINE / "demo"
KEY_FIELDS = ("api_key", "base_url", "model", "backup_key", "backup_base_url", "backup_model")
DATE = re.compile(r"(?<!\d)(20\d\d)-(\d\d)-(\d\d)(?!\d)")


def shift(text, days):
    def move(m):
        try:
            return (date(int(m[1]), int(m[2]), int(m[3])) + timedelta(days=days)).isoformat()
        except ValueError:
            return m[0]
    return DATE.sub(move, text)


def main(MINE=MINE, DEMO=DEMO):
    if not (SEED / "seed.json").exists():
        sys.exit("demo/seed is missing. Run: .venv/bin/python demo/build_seed.py")
    built = date.fromisoformat(json.loads((SEED / "seed.json").read_text())["built"])
    days = (date.today() - built).days
    if DEMO.exists():
        shutil.rmtree(DEMO)
    DEMO.mkdir(parents=True)
    for f in SEED.glob("*.json"):
        if f.name != "seed.json":
            (DEMO / f.name).write_text(shift(f.read_text(encoding="utf-8"), days), encoding="utf-8")

    # Today's plan is written fresh (it names weekdays, and should include today's matches); demo.sh asks for it.
    mentor = json.loads((DEMO / "mentor.json").read_text(encoding="utf-8"))
    for k in ("plan", "plan_date", "done"):
        mentor.pop(k, None)
    (DEMO / "mentor.json").write_text(json.dumps(mentor, ensure_ascii=False), encoding="utf-8")

    mine = json.loads((MINE / "settings.json").read_text(encoding="utf-8")) if (MINE / "settings.json").exists() else {}
    settings = {k: mine[k] for k in KEY_FIELDS if mine.get(k)}
    settings.update({"automatch": "on", "automatch_prepare": "2", "automatch_depth": "docs", "discover_searches": "0", "alert_auto": "off"})
    (DEMO / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")

    db = MINE / "jobs_db.sqlite"
    if db.exists():
        src, dst = sqlite3.connect(db), sqlite3.connect(DEMO / "jobs_db.sqlite")
        src.backup(dst)
        src.close()
        dst.execute("DELETE FROM postings WHERE board = 'email'")  # your own job-alert emails
        dst.execute("DELETE FROM sources WHERE name IN ('Job alert emails', 'Daily matches')")
        dst.commit()
        dst.close()
    for shared in ("onet.sqlite", "qlib", "official"):  # public downloads and caches, shared read-mostly
        if (MINE / shared).exists():
            try:
                os.symlink(MINE / shared, DEMO / shared)
            except OSError:
                (shutil.copytree if (MINE / shared).is_dir() else shutil.copy)(MINE / shared, DEMO / shared)
    print(f"Demo ready in {DEMO} (dates moved {days} days; AI key {'copied' if settings.get('api_key') else 'missing: AI features will ask for one'})")


if __name__ == "__main__":
    main()
