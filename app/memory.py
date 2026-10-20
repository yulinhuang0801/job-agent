"""Shared memory: one place for facts about the candidate and a log of what happened, read by every
agent (intake interview, Stories, Skills check, mentors, email and form writers, profile updates).

- facts: short, durable things the candidate said or decided ("Wants remote roles only",
  "Got an interview at Palazzo on Nov 3"). Agents save them automatically; the candidate can
  see and delete any of them.
- events: what happened in the app (profile updated, story finished, email sent...).
- pins: profile fields the candidate or a coach edited by hand. Profile updates never change them.
"""
import uuid
from datetime import datetime

from . import store

MAX_FACTS = 300
MAX_EVENTS = 300


def _load():
    return {"facts": [], "events": [], "pins": [], **store.load("memory", {})}


def _now():
    return datetime.now().isoformat(timespec="seconds")


def facts():
    return _load()["facts"]


def add_facts(texts, source):
    """Save new facts (skipping duplicates). Returns the saved fact records."""
    data = _load()
    known = {f["text"].strip().lower() for f in data["facts"]}
    saved = []
    for t in texts or []:
        t = str(t).strip()
        if len(t) < 3 or t.lower() in known:
            continue
        rec = {"id": uuid.uuid4().hex[:8], "text": t[:300], "source": source, "at": _now()}
        data["facts"].append(rec)
        known.add(t.lower())
        saved.append(rec)
    if saved:
        data["facts"] = data["facts"][-MAX_FACTS:]
        store.save("memory", data)
    return saved


def forget(fact_id):
    data = _load()
    data["facts"] = [f for f in data["facts"] if f["id"] != fact_id]
    store.save("memory", data)
    return data["facts"]


def log(kind, text, ref=""):
    data = _load()
    data["events"].append({"at": _now(), "kind": kind, "text": str(text)[:300], "ref": ref})
    data["events"] = data["events"][-MAX_EVENTS:]
    store.save("memory", data)


def events(n=30):
    return _load()["events"][-n:]


def shared_block(max_facts=60, max_events=15):
    """Text every agent gets: what the team knows and what happened recently."""
    data = _load()
    lines = []
    if data["facts"]:
        lines.append("WHAT THE TEAM KNOWS ABOUT THE CANDIDATE (newest last; newer facts win over older ones and over the profile):")
        lines += [f"- [{f['at'][:10]}, from {f['source']}] {f['text']}" for f in data["facts"][-max_facts:]]
    if data["events"]:
        lines.append("RECENT ACTIVITY:")
        lines += [f"- {e['at'][:16].replace('T', ' ')}: {e['text']}" for e in data["events"][-max_events:]]
    return "\n".join(lines)


# ---------- Pins: profile fields edited by hand ----------

def pins():
    return set(_load()["pins"])


def pin(keys):
    data = _load()
    current = set(data["pins"])
    new = [k for k in keys if k and k not in current]
    if new:
        data["pins"] = sorted(current | set(new))
        store.save("memory", data)
