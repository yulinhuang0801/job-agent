"""The mentor team: a lead mentor who knows where the user stands and plans each day, plus a
behavioral coach, a technical coach and a resume coach. Coaches suggest actions as buttons; nothing
changes until the user clicks one."""
import json
from datetime import date, datetime, timedelta

from . import assess, db, llm, memory, pipeline, prep, profile_merge, prompts, store, timelines

COACHES = {
    "lead": {"name": "Lead mentor", "system": "LEAD_SYSTEM"},
    "behavioral": {"name": "Behavioral coach", "system": "BEHAVIORAL_COACH_SYSTEM"},
    "technical": {"name": "Technical coach", "system": "TECHNICAL_COACH_SYSTEM"},
    "resume": {"name": "Resume coach", "system": "RESUME_COACH_SYSTEM"},
}
TABS = {"mentors", "interview", "stories", "skills", "profile", "find", "jobs", "tracker", "network", "calendar", "guide", "settings"}
# Cards an "open" button can scroll to, and the page each one is on (ids in static/app.js SECTIONS).
SECTIONS = {"alerts": "settings", "outreach": "jobs", "add_job": "jobs", "data_sources": "find",
            "company_prep": "skills", "my_behavioral": "stories", "my_technical": "skills"}
DEFAULT_GOAL = 10  # firms contacted per day, from the boutique internship guide
HISTORY_TURNS = 30


# ---------- Where the user stands ----------

def _today():
    return date.today().isoformat()


def snapshot():
    """Plain facts about the user's progress, computed from their data (no AI)."""
    today = _today()
    week_ago = (date.today() - timedelta(days=6)).isoformat()
    profile = store.load("profile", {})
    interview = store.load("interview", {})
    stories = assess.public_stories(assess.load_stories())
    skills = assess.load_skills()
    jobs = store.load("jobs", [])
    outreach = store.load("outreach", {})
    mentor = load_mentor()

    by_status = {}
    for j in jobs:
        by_status[j["status"]] = by_status.get(j["status"], 0) + 1
    contacted = [j for j in jobs if j.get("contacted_date") or j["status"] in ("Contacted", "Followed up", "Applied", "Online assessment", "Interviewing", "Offer", "Accepted")]
    due = [j for j in jobs if j.get("followup_due") and j["followup_due"] <= today and j["status"] == "Contacted"]
    ready_stories = [c for c in stories["plan"] if stories["stories"][c]["status"] == "done"]
    thin_stories = [{"type": stories["labels"][c], "missing": stories["stories"][c].get("missing", [])}
                    for c in stories["plan"] if stories["stories"][c]["status"] in ("pending", "active")]
    graded = skills["status"] == "graded"

    return {
        "today": today, "weekday": date.today().strftime("%A"),
        "goal_per_day": mentor["goal_per_day"],
        "profile_built": bool(profile),
        "targets": profile.get("targets") or {},
        "evaluation": profile.get("evaluation") or {},
        # The app's first-step intake chat (not a job interview).
        "intake_chat": {"progress": interview.get("progress", 0), "complete": bool(interview.get("complete")),
                        "cv_uploaded": bool(interview.get("resume_name"))},
        "stories": {"ready": len(ready_stories), "total": len(stories["plan"]),
                    "ready_types": [stories["labels"][c] for c in ready_stories], "need_work": thin_stories[:10]},
        "skills": {"status": skills["status"],
                   "score": f"{skills['results']['total']}/{skills['results']['max']}" if graded else None,
                   "levels": (skills["results"] or {}).get("skills", []) if graded else [],
                   "weak_questions": [q["area"] for q, g in zip(skills["questions"], skills["results"]["grades"]) if g["score"] <= 1] if graded else []},
        "company_preps": [f"{p['company']} ({p['role']})" for p in prep.load_preps()],
        "outreach_details_set": bool(outreach.get("intro") and outreach.get("examples")),
        "jobs": {
            "total": len(jobs), "by_status": by_status,
            "added_today": sum(1 for j in jobs if j["created_at"][:10] == today),
            "contacted_today": sum(1 for j in jobs if (j.get("contacted_date") or "") == today),
            "contacted_this_week": sum(1 for j in jobs if (j.get("contacted_date") or "") >= week_ago),
            "contacted_total": len(contacted),
            "followups_due": [{"job_id": j["id"], "company": j["company"], "contact": (j.get("contact") or {}).get("name", ""), "due": j["followup_due"]} for j in due],
            "not_researched": [{"job_id": j["id"], "company": j["company"]} for j in jobs if j.get("kind") == "firm" and not j.get("research") and j["status"] == "Saved"][:8],
            "missing_email": [{"job_id": j["id"], "company": j["company"]} for j in jobs if j.get("research") and not (j.get("contact") or {}).get("email") and j["status"] == "Saved"][:8],
            "email_written_not_sent": [{"job_id": j["id"], "company": j["company"]} for j in jobs if j.get("email") and not j.get("contacted_date")][:8],
            "interviewing": [{"job_id": j["id"], "company": j["company"], "title": j.get("title", "")} for j in jobs if j["status"] == "Interviewing"],
        },
        "coach_notes": {c: mentor["notes"].get(c, [])[-3:] for c in COACHES if c != "lead"},
        "team_memory": [f"[{f['at'][:10]}, {f['source']}] {f['text']}" for f in memory.facts()[-40:]],
        "recent_activity": [f"{e['at'][:16].replace('T', ' ')}: {e['text']}" for e in memory.events(15)],
        # Dated items from every job and the networking list: deadlines, assessments, interviews, thank-yous, offers.
        "pipeline": pipeline.summary_for_mentors(),
        "jobs_index": [{"job_id": j["id"], "company": j.get("company"), "title": j.get("title", ""), "status": j["status"]} for j in jobs[:60]],
        "weak_graded_answers": [{"type": stories["labels"][c], "stars": stories["stories"][c]["grade"]["stars"],
                                 "fix": stories["stories"][c]["grade"].get("top_fix", "")}
                                for c in stories["plan"] if (stories["stories"][c].get("grade") or {}).get("stars", 5) <= 3][:8],
        "recruiting_windows": _windows(profile),
        "alert_postings_this_week": _alert_postings(week_ago),
    }


def _windows(profile):
    """Recruiting windows for the candidate's fields that are open now or open within 2 months."""
    try:
        t = timelines.for_profile(profile)
    except Exception:
        return []
    out = []
    for f in t["fields"]:
        if f["yours"]:
            for w in f["windows"]:
                if w["open_now"] or w["starts_in_months"] <= 2:
                    out.append(f"{f['label']}: {w['what']} ({w['from']} – {w['to']}, summer {w['summer']}{', ' + w['summer_kind'] if w['summer_kind'] else ''})")
    return out[:6]


def _alert_postings(since):
    try:
        with db.connect() as con:
            return con.execute("SELECT COUNT(*) FROM postings WHERE board = 'email' AND seen >= ?", (since,)).fetchone()[0]
    except Exception:
        return 0


def _upcoming(kinds=("Online assessment", "Interview", "Coffee chat", "Info session", "Career fair")):
    return [i for i in pipeline.calendar(21) if i["kind"] in kinds][:8]


def milestones(s):
    """The program's checklist, in order, so the user can see where they are."""
    steps = [
        ("Intake interview", s["intake_chat"]["complete"] or s["profile_built"]),
        ("Profile built", s["profile_built"]),
        ("5+ behavioral answers ready", s["stories"]["ready"] >= 5),
        ("Skills check graded", s["skills"]["status"] == "graded"),
        ("Outreach details set", s["outreach_details_set"]),
        ("First 10 firms contacted", s["jobs"]["contacted_total"] >= 10),
        ("First interview", bool(s["jobs"]["interviewing"]) or s["jobs"]["by_status"].get("Offer", 0) > 0),
    ]
    return [{"label": label, "done": bool(done)} for label, done in steps]


# ---------- Mentor state ----------

def load_mentor():
    return {"goal_per_day": DEFAULT_GOAL, "plan": None, "plan_date": "", "done": [], "notes": {}, "chats": {}, **store.load("mentor", {})}


def save_mentor(m):
    store.save("mentor", m)


def _clean_actions(actions):
    """Keep only actions the page knows how to run, with the fields they need."""
    out = []
    for a in actions or []:
        if not isinstance(a, dict) or not a.get("label"):
            continue
        t = a.get("type")
        if t == "open" and (a.get("tab") in TABS or a.get("section") in SECTIONS):
            section = a.get("section") if a.get("section") in SECTIONS else None
            tab = SECTIONS[section] if section else a["tab"]  # the section decides the page, so the two can't disagree
            out.append({"type": t, "tab": tab, "label": a["label"], **({"section": section} if section else {})})
        elif t == "coach" and a.get("coach") in COACHES:
            out.append({"type": t, "coach": a["coach"], "label": a["label"], "message": str(a.get("message") or "")})
        elif t == "job" and a.get("job_id"):
            out.append({"type": t, "job_id": str(a["job_id"]), "label": a["label"]})
        elif t == "company_prep" and a.get("company"):
            out.append({"type": t, "company": a["company"], "role": str(a.get("role") or ""), "label": a["label"]})
        elif t == "skills_check":
            out.append({"type": t, "areas": [str(x) for x in a.get("areas") or []], "count": int(a.get("count") or 5), "label": a["label"]})
        elif t == "edit_profile" and a.get("path") and a.get("new"):
            out.append({"type": t, "path": str(a["path"]), "old": str(a.get("old") or ""), "new": str(a["new"]), "label": a["label"]})
        elif t == "save_story" and a.get("competency") and isinstance(a.get("fields"), dict):
            out.append({"type": t, "competency": a["competency"], "fields": {k: str(v) for k, v in a["fields"].items()}, "label": a["label"]})
        elif t == "add_profile_item" and a.get("section") in profile_merge.LIST_SECTIONS and isinstance(a.get("item"), dict):
            out.append({"type": t, "section": a["section"], "item": a["item"], "label": a["label"]})
        elif t == "update_targets" and isinstance(a.get("targets"), dict) and a["targets"]:
            out.append({"type": t, "targets": a["targets"], "label": a["label"]})
        elif t == "update_skill" and a.get("name"):
            out.append({"type": t, "name": str(a["name"]), "level": str(a.get("level") or ""), "evidence": str(a.get("evidence") or ""), "label": a["label"]})
        elif t == "update_job" and a.get("job_id"):
            out.append({"type": t, "job_id": str(a["job_id"]), "label": a["label"],
                        **{k: str(a[k]) for k in ("status", "notes", "contact_email") if a.get(k)}})
        elif t == "add_event" and (a.get("job_id") or a.get("company")) and a.get("date"):
            out.append({"type": t, "label": a["label"], **{k: str(a.get(k) or "") for k in ("job_id", "company", "event_type", "date", "time", "title", "platform")}})
        elif t == "add_contact" and a.get("name"):
            out.append({"type": t, "label": a["label"], **{k: str(a.get(k) or "") for k in ("name", "company", "title", "how", "job_id", "notes")}})
        elif t == "add_question" and a.get("kind") in ("behavioral", "technical") and a.get("question"):
            out.append({"type": t, "label": a["label"], **{k: str(a.get(k) or "") for k in ("kind", "question", "area", "rubric", "look_for")}})
    return out[:4]


# ---------- Today's plan (lead mentor) ----------

def today_plan(refresh=False):
    m = load_mentor()
    s = snapshot()
    if refresh or m["plan_date"] != s["today"] or not m["plan"]:
        out = llm.chat_json([
            {"role": "system", "content": prompts.TODAY_SYSTEM},
            {"role": "user", "content": "STATUS:\n" + json.dumps(s, ensure_ascii=False)},
        ], max_tokens=6000, temperature=0.5, timeout=120)
        tasks = []
        for t in out.get("tasks") or []:
            if t.get("title"):
                acts = _clean_actions([t.get("action")] if t.get("action") else [])
                tasks.append({"title": str(t["title"]), "why": str(t.get("why") or ""), "minutes": int(t.get("minutes") or 0),
                              "action": acts[0] if acts else None})
        m.update({"plan": {"headline": out.get("headline") or "", "stage": out.get("stage") or "", "status": out.get("status") or "",
                           "tasks": tasks[:6], "encouragement": out.get("encouragement") or ""},
                  "plan_date": s["today"], "done": []})
        save_mentor(m)
    return {"plan": m["plan"], "done": m["done"], "milestones": milestones(s), "goal_per_day": m["goal_per_day"],
            "counts": {"contacted_today": s["jobs"]["contacted_today"], "contacted_this_week": s["jobs"]["contacted_this_week"],
                       "followups_due": len(s["jobs"]["followups_due"]), "jobs_total": s["jobs"]["total"]},
            "coaches": {k: v["name"] for k, v in COACHES.items()}}


def mark_task(index, done):
    m = load_mentor()
    done_set = set(m["done"])
    done_set.add(index) if done else done_set.discard(index)
    m["done"] = sorted(done_set)
    save_mentor(m)
    return m["done"]


def set_goal(n):
    m = load_mentor()
    m["goal_per_day"] = max(1, min(50, int(n)))
    save_mentor(m)
    return m["goal_per_day"]


# ---------- Coach chats ----------

def _context(coach, s):
    """What each coach sees: the lead sees everything; the others see what their job needs."""
    profile = store.load("profile", {})
    if coach == "lead":
        return {"status": s}
    if coach == "behavioral":
        st = assess.load_stories()
        comps = assess.comps()
        answers = {}
        for c in st["plan"]:
            if c not in st["stories"]:
                continue
            e = st["stories"][c]
            a = {k: v for k, v in e.items() if k in ("title", "situation", "task", "action", "result", "reflection", "answer", "quality", "missing", "status")}
            g = e.get("grade")
            if g:  # the rubric grade, so coaching starts from the weakest dimension
                a["rubric"] = {"stars": g["stars"], "scores": {d["label"]: d["score"] for d in g["dims"]}, "biggest_fix": g.get("top_fix", "")}
            answers[comps[c]["label"]] = a
        return {"targets": s["targets"], "answers": answers, "company_preps": s["company_preps"], "upcoming": _upcoming(),
                "jobs_index": s["jobs_index"],
                "experience": [(e.get("company"), e.get("title")) for e in profile.get("experience", [])]}
    if coach == "technical":
        sk = assess.load_skills()
        return {"targets": s["targets"], "skills_profile": (profile.get("skills") or {}), "skills_check": {
                    "status": sk["status"], "results": sk["results"],
                    "questions": [{k: q.get(k) for k in ("id", "area", "level", "type", "question")} for q in sk["questions"]]},
                "areas_in_bank": [a["label"] for a in assess.skill_areas() if a.get("source") == "Job Agent"],
                "company_preps": s["company_preps"], "upcoming": _upcoming(), "jobs_index": s["jobs_index"]}
    if coach == "resume":
        keep = {k: profile.get(k) for k in ("name", "targets", "summary", "education", "experience", "projects", "skills")}
        return {"profile": keep, "evaluation": s["evaluation"], "jobs_applying_to": [
            {"company": j["company"], "title": j.get("title"), "fit": (j.get("fit") or {}).get("score")} for j in store.load("jobs", [])[:10]]}
    return {}


def _for_model(m):
    if m["role"] == "user":
        return {"role": "user", "content": m["content"]}
    # DeepSeek's JSON mode needs earlier assistant turns in JSON shape too.
    return {"role": "assistant", "content": json.dumps({"reply": m["content"]}, ensure_ascii=False)}


def chat(coach):
    if coach not in COACHES:
        raise llm.LLMError("Unknown coach.")
    return {"coach": coach, "name": COACHES[coach]["name"], "messages": load_mentor()["chats"].get(coach, [])}


def coach_turn(coach, text):
    if coach not in COACHES:
        raise llm.LLMError("Unknown coach.")
    m = load_mentor()
    s = snapshot()
    history = m["chats"].get(coach, [])
    shared = memory.shared_block(max_facts=60, max_events=15) if coach != "lead" else ""  # the lead's snapshot already has it
    system = getattr(prompts, COACHES[coach]["system"]) + prompts.COACH_ACTIONS + "\n\nCONTEXT (today is " + s["today"] + "):\n" \
        + json.dumps(_context(coach, s), ensure_ascii=False)[:30000] + ("\n\nSHARED MEMORY:\n" + shared if shared else "")
    msgs = [{"role": "system", "content": system}] + [_for_model(x) for x in history[-HISTORY_TURNS:]] + [{"role": "user", "content": text}]
    out = llm.chat_json(msgs, max_tokens=6000, temperature=0.6, timeout=120)
    reply = str(out.get("reply") or "").strip()
    if not reply:
        raise llm.LLMError("The coach returned an empty reply. Please send your message again.")
    remembered = memory.add_facts(out.get("memory") or [], COACHES[coach]["name"].lower())
    history = history + [{"role": "user", "content": text, "at": datetime.now().isoformat(timespec="seconds")},
                         {"role": "assistant", "content": reply, "actions": _clean_actions(out.get("actions")),
                          "remembered": remembered, "at": datetime.now().isoformat(timespec="seconds")}]
    m = load_mentor()  # re-read: the user may have ticked tasks meanwhile
    m["chats"][coach] = history[-200:]
    note = str(out.get("progress_note") or "").strip()
    if note and coach != "lead":
        m["notes"].setdefault(coach, []).append(f"{s['today']}: {note}")
        m["notes"][coach] = m["notes"][coach][-10:]
    save_mentor(m)
    return chat(coach)


def clear_chat(coach):
    m = load_mentor()
    m["chats"][coach] = []
    save_mentor(m)
    return chat(coach)


# ---------- Actions that change data (run only when the user clicks) ----------

def edit_profile(path, old, new):
    """Replace one text field in the profile, e.g. path "experience.1.bullets.2"."""
    profile = store.load("profile", {})
    node, keys = profile, path.split(".")
    try:
        for k in keys[:-1]:
            node = node[int(k)] if isinstance(node, list) else node[k]
        last = int(keys[-1]) if isinstance(node, list) else keys[-1]
        current = node[last]
    except (KeyError, IndexError, ValueError, TypeError):
        raise llm.LLMError("That part of your profile doesn't exist anymore. Ask the coach again.")
    if old and str(current).strip() != old.strip():
        raise llm.LLMError("That line has changed since the coach suggested this. Ask the coach again.")
    node[last] = new
    memory.pin([profile_merge.path_to_pin(profile, path)])
    store.save("profile", profile)
    memory.log("profile", f"Applied a coach's rewrite to {path}")
    return profile


def save_story(competency, fields):
    state = assess.load_stories()
    if competency not in assess.comps():
        raise llm.LLMError("Unknown question type.")
    if competency not in state["plan"]:
        state["plan"].append(competency)
    entry = state["stories"].setdefault(competency, assess._new_entry(competency))
    for k, v in fields.items():
        if k in assess.ALL_FIELDS and v:
            entry[k] = v
    entry["status"] = "done"
    entry["missing"] = []
    store.save("stories", state)
    memory.log("story", f"Saved a coach's version of the \"{assess.comps()[competency]['label']}\" answer")
    return assess.public_stories(assess.load_stories())


def add_profile_item(section, item):
    profile = store.load("profile", {})
    if not profile:
        raise llm.LLMError("Build your profile first.")
    # The candidate chose this item themselves, so only exact duplicates are refused.
    profile, changes = profile_merge.apply_patch(profile, {"add": {section: [item]}}, memory.pins(), guard_similar=False)
    if not changes:
        raise llm.LLMError("That's already in your profile.")
    store.save("profile", profile)
    memory.log("profile", changes[0])
    return profile


def update_targets(targets):
    profile = store.load("profile", {})
    profile.setdefault("targets", {}).update({k: v for k, v in targets.items() if v not in (None, "", [])})
    memory.pin([f"targets.{k}" for k in targets])
    store.save("profile", profile)
    memory.log("profile", "Updated targets: " + ", ".join(targets))
    return profile


def update_skill(name, level, evidence):
    profile = store.load("profile", {})
    tech = profile.setdefault("skills", {}).setdefault("technical", [])
    current = next((x for x in tech if isinstance(x, dict) and str(x.get("name", "")).lower() == name.lower()), None)
    if current is None:
        current = {"name": name}
        tech.append(current)
    current.update({k: v for k, v in {"level": level, "evidence": evidence}.items() if v})
    memory.pin([f"skills::{name.lower()}"])
    store.save("profile", profile)
    memory.log("profile", f"Skill updated: {name}" + (f" → {level}" if level else ""))
    return profile


def apply(action):
    """Run a coach's suggested update after the candidate clicked it."""
    t = action.get("type")
    if t == "edit_profile":
        edit_profile(action["path"], action.get("old", ""), action["new"])
    elif t == "save_story":
        save_story(action["competency"], action["fields"])
    elif t == "add_profile_item":
        add_profile_item(action["section"], action["item"])
    elif t == "update_targets":
        update_targets(action["targets"])
    elif t == "update_skill":
        update_skill(action["name"], action.get("level", ""), action.get("evidence", ""))
    elif t == "add_question":
        item = {"question": action["question"]}
        if action["kind"] == "technical":
            item.update(area=action.get("area") or "From my coaches", rubric=action.get("rubric", ""))
        else:
            item.update(look_for=action.get("look_for", ""), label=action["question"][:60], group="From my coaches")
        assess.add_custom(action["kind"], item)
        memory.log("question", f"Added a {action['kind']} question: {action['question'][:80]}")
    elif t == "add_event":
        job_id = action.get("job_id") or _job_for_company(action.get("company", ""))
        pipeline.add_event(job_id, {"type": action.get("event_type") or "other", "date": action.get("date"), "time": action.get("time", ""),
                                    "title": action.get("title", ""), "platform": action.get("platform", "")})
    elif t == "add_contact":
        pipeline.add_person({k: action.get(k) for k in ("name", "company", "title", "how", "job_id", "notes") if action.get(k)})
    else:
        raise llm.LLMError("This button can't change anything.")
    return {"ok": True}


def _job_for_company(company):
    """The tracked job for this company, or a new one so the date has somewhere to live."""
    company = (company or "").strip()
    if not company:
        raise llm.LLMError("Which company is this for?")
    jobs = store.load("jobs", [])
    match = next((j for j in jobs if (j.get("company") or "").strip().lower() == company.lower()), None)
    if match:
        return match["id"]
    import uuid
    job = {"id": uuid.uuid4().hex[:10], "created_at": datetime.now().isoformat(timespec="seconds"), "url": "", "title": "", "company": company,
           "location": "", "description": "", "status": "Applied", "applied_date": "", "notes": "Added by a mentor from your chat.", "fit": None, "documents": None}
    jobs.insert(0, job)
    store.save("jobs", jobs)
    memory.log("job", f"Started tracking {company} (added by a mentor)", job["id"])
    return job["id"]
