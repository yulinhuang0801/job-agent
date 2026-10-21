"""The later stages of recruiting, after the first email or application:

- events on each job (online assessments, interviews, coffee chats, info sessions, deadlines),
  gathered into one calendar with an .ics export;
- online-assessment prep by platform (HireVue, CodeSignal, HackerRank, games, reasoning tests);
- thank-you notes after interviews, and offers (compare, ask for more time, negotiate, accept, decline);
- a networking tracker: alumni, recruiters and referrals, with messages and coffee-chat prep.

Every message is a draft the user sends from their own mailbox or LinkedIn."""
import json
import uuid
from datetime import date, datetime, timedelta

from . import assess, llm, memory, prompts, store

EVENT_TYPES = {
    "oa": "Online assessment", "interview": "Interview", "coffee_chat": "Coffee chat", "info_session": "Info session",
    "career_fair": "Career fair", "deadline": "Deadline", "other": "Other",
}
OA_PLATFORMS = {
    "hirevue": {"label": "HireVue / one-way video interview", "kind": "video",
                "tips": ["You record answers to preset questions; there's usually a short preparation time (often 30 seconds) and 1-3 minutes per answer. Some employers allow a retake, many don't.",
                         "Questions are mostly behavioral and \"why us / why this role\"; banks sometimes add a technical question.",
                         "Recruiters (and sometimes software) review what you say and how clearly you say it: answer the question in the first sentence, then one STAR example, then a short close.",
                         "Test camera, light and sound first; look at the camera, not your face on screen; keep notes to three keywords per answer."]},
    "codesignal": {"label": "CodeSignal coding assessment", "kind": "coding",
                   "tips": ["Usually about four coding tasks of increasing difficulty in roughly 70 minutes; score depends on tests passed and speed.",
                            "Do the easy tasks fast and fully, then spend the rest on the harder ones; partial credit counts.",
                            "Practice arrays, strings, hash maps and simple simulation; the last task often needs an efficient approach.",
                            "Use the language you're fastest in, and run the provided tests before submitting."]},
    "hackerrank": {"label": "HackerRank coding test", "kind": "coding",
                   "tips": ["Typically 1-3 problems in 60-90 minutes, graded on hidden test cases; some include SQL or multiple choice.",
                            "Read input/output format carefully: many failures are formatting, not logic.",
                            "Get a correct brute-force solution in first, then optimize if time allows.",
                            "Common topics: arrays, strings, hash maps, sorting, two pointers, BFS/DFS, basic dynamic programming."]},
    "codility": {"label": "Codility test", "kind": "coding",
                 "tips": ["Usually 2-3 tasks; correctness and performance on large inputs are both scored.",
                          "Think about edge cases (empty input, one element, very large numbers) before submitting.",
                          "Aim for O(n) or O(n log n) on the performance tests."]},
    "games": {"label": "Pymetrics / Arctic Shores games", "kind": "games",
              "tips": ["Short games that measure traits like attention, memory and risk-taking. There are no right answers.",
                       "Play naturally and consistently, in a quiet place, without rushing. Trying to game it usually backfires.",
                       "Read each game's instructions fully; practice rounds don't count."]},
    "reasoning": {"label": "SHL / Aon / Watson-Glaser reasoning test", "kind": "reasoning",
                  "tips": ["Timed numerical, verbal and logical reasoning questions; speed matters as much as accuracy.",
                           "For numerical tests, have a calculator and scratch paper ready, and practice percentages, ratios and reading tables.",
                           "For verbal tests, answer only from the passage, not from what you know.",
                           "Do a few free practice tests first to learn the formats."]},
    "modeling": {"label": "Excel / financial modeling test", "kind": "modeling",
                 "tips": ["Usually build a three-statement model, a DCF or a simple LBO from a prompt in 1-3 hours.",
                          "Lay out assumptions first, keep formulas consistent across columns, and check that the balance sheet balances.",
                          "Practice Excel shortcuts; time pressure is the main difficulty."]},
    "case": {"label": "Written case", "kind": "case",
             "tips": ["You read data on a business problem and write or present a recommendation in limited time.",
                      "Lead with the answer, support it with 2-3 numbers from the data, and name the main risk.",
                      "Practice structuring: profitability, market entry, growth."]},
    "other": {"label": "Other assessment", "kind": "other", "tips": ["Ask the recruiter what format to expect, and practice that format."]},
}
NETWORK_STATUSES = ["To reach out", "Messaged", "No reply", "Chatted", "Thanked", "Referred"]
HOW_MET = ["Alumni", "Recruiter", "Referral", "Met at an event", "Professor or advisor", "Friend or family", "Cold"]


def _today():
    return date.today().isoformat()


def _jobs():
    return store.load("jobs", [])


def _find(jobs, job_id):
    job = next((j for j in jobs if j["id"] == job_id), None)
    if not job:
        raise llm.LLMError("Job not found.")
    return job


def _clean_date(v):
    v = str(v or "").strip()[:10]
    try:
        return datetime.strptime(v, "%Y-%m-%d").date().isoformat() if v else ""
    except ValueError:
        raise llm.LLMError("Dates must look like 2026-11-03.")


# ---------- Events on a job ----------

STATUS_ORDER = ["Saved", "Ready", "Contacted", "Followed up", "Applied", "Online assessment", "Interviewing", "Offer", "Accepted"]


def _advance(job, status):
    """Move a job forward (never backward, never out of Rejected/Withdrawn)."""
    cur = job.get("status", "Saved")
    if cur in ("Rejected", "Withdrawn") or cur not in STATUS_ORDER:
        return
    if STATUS_ORDER.index(status) > STATUS_ORDER.index(cur):
        memory.log("job", f"{job.get('company')}: {cur} → {status}", job["id"])
        job["status"] = status


def add_event(job_id, data):
    jobs = _jobs()
    job = _find(jobs, job_id)
    etype = data.get("type") if data.get("type") in EVENT_TYPES else "other"
    ev = {"id": uuid.uuid4().hex[:8], "type": etype, "title": str(data.get("title") or "").strip()[:120],
          "date": _clean_date(data.get("date")), "time": str(data.get("time") or "")[:5], "notes": str(data.get("notes") or "")[:2000],
          "platform": data.get("platform") if data.get("platform") in OA_PLATFORMS else ("" if etype != "oa" else "other"),
          "people": str(data.get("people") or "")[:300], "done": False, "created_at": datetime.now().isoformat(timespec="seconds")}
    if not ev["date"]:
        raise llm.LLMError("Pick a date.")
    job.setdefault("events", []).append(ev)
    if etype == "oa":
        _advance(job, "Online assessment")
    elif etype == "interview":
        _advance(job, "Interviewing")
    memory.log("event", f"{job.get('company')}: {EVENT_TYPES[etype]}{' (' + ev['title'] + ')' if ev['title'] else ''} on {ev['date']}", job["id"])
    store.save("jobs", jobs)
    return job


def update_event(job_id, event_id, data):
    jobs = _jobs()
    job = _find(jobs, job_id)
    ev = next((e for e in job.get("events") or [] if e["id"] == event_id), None)
    if not ev:
        raise llm.LLMError("Event not found.")
    for k in ("title", "time", "notes", "people"):
        if k in data and data[k] is not None:
            ev[k] = str(data[k])[:2000]
    if data.get("date"):
        ev["date"] = _clean_date(data["date"])
    if "done" in data and data["done"] is not None:
        ev["done"] = bool(data["done"])
    if data.get("platform") in OA_PLATFORMS:
        ev["platform"] = data["platform"]
    if "thanks_sent" in data and ev.get("thank_you"):
        ev["thank_you"]["sent"] = bool(data["thanks_sent"])
    store.save("jobs", jobs)
    return job


def delete_event(job_id, event_id):
    jobs = _jobs()
    job = _find(jobs, job_id)
    job["events"] = [e for e in job.get("events") or [] if e["id"] != event_id]
    store.save("jobs", jobs)
    return job


# ---------- Online-assessment and interview prep ----------

def _candidate():
    return assess.candidate_background(4000)


def event_prep(job_id, event_id):
    jobs = _jobs()
    job = _find(jobs, job_id)
    ev = next((e for e in job.get("events") or [] if e["id"] == event_id), None)
    if not ev:
        raise llm.LLMError("Event not found.")
    platform = OA_PLATFORMS.get(ev.get("platform") or "", None) if ev["type"] == "oa" else None
    stories = assess.public_stories(assess.load_stories())
    ready = [stories["labels"][c] for c in stories["plan"] if stories["stories"][c]["status"] == "done"]
    areas = [a["label"] for a in assess.skill_areas() if a.get("source") == "Job Agent"]
    lc_topics = [a["id"] for a in assess.skill_areas() if a.get("source") == "LeetCode"]
    out = llm.chat_json([
        {"role": "system", "content": prompts.EVENT_PREP_SYSTEM},
        {"role": "user", "content": json.dumps({
            "event": {"type": EVENT_TYPES[ev["type"]], "title": ev["title"], "date": ev["date"], "notes": ev["notes"], "people": ev.get("people"),
                      "platform": platform["label"] if platform else "", "platform_tips": platform["tips"] if platform else []},
            "days_until": (date.fromisoformat(ev["date"]) - date.today()).days,
            "job": {"company": job.get("company"), "title": job.get("title"), "posting": (job.get("description") or "")[:3000],
                    "research": (job.get("research") or {}).get("summary", "")},
            "candidate": _candidate(), "stories_ready": ready,
            "skills_check_areas": areas, "leetcode_topic_ids": lc_topics}, ensure_ascii=False)},
    ], max_tokens=6000, temperature=0.3, timeout=150)
    ev["prep"] = {"summary": out.get("summary", ""), "steps": out.get("steps") or [], "practice_questions": out.get("practice_questions") or [],
                  "tips": platform["tips"] if platform else [], "made": _today()}
    store.save("jobs", jobs)
    return job


# ---------- Drafted messages ----------

DRAFT_KINDS = {
    "thank_you": "Thank-you email after an interview",
    "extension": "Ask for more time to decide on an offer",
    "negotiate": "Ask, politely, whether the offer can be improved",
    "accept": "Accept the offer",
    "decline": "Decline the offer gracefully, keeping the door open",
    "coffee_chat": "Ask for a 15-20 minute call (LinkedIn note and email)",
    "follow_up": "Follow up on a message with no reply",
    "chat_thanks": "Thank-you after a coffee chat",
    "referral": "Ask for a referral to a specific role after a chat",
}


def draft(kind, context):
    if kind not in DRAFT_KINDS:
        raise llm.LLMError("Unknown message type.")
    outreach = store.load("outreach", {})
    out = llm.chat_json([
        {"role": "system", "content": prompts.DRAFT_SYSTEM},
        {"role": "user", "content": json.dumps({"kind": DRAFT_KINDS[kind], "context": context, "candidate": _candidate(),
                                                "signature": outreach.get("signature", "")}, ensure_ascii=False)},
    ], max_tokens=3000, temperature=0.5, timeout=120)
    return {"kind": kind, "subject": str(out.get("subject") or ""), "body": str(out.get("body") or ""),
            "linkedin_note": str(out.get("linkedin_note") or "")[:300], "made": _today()}


def thank_you(job_id, event_id, notes, people):
    jobs = _jobs()
    job = _find(jobs, job_id)
    ev = next((e for e in job.get("events") or [] if e["id"] == event_id), None)
    if not ev:
        raise llm.LLMError("Event not found.")
    if people:
        ev["people"] = people[:300]
    if notes:
        ev["notes"] = notes[:2000]
    ev["thank_you"] = {**draft("thank_you", {"company": job.get("company"), "role": job.get("title"), "round": ev.get("title") or EVENT_TYPES[ev["type"]],
                                             "date": ev["date"], "interviewers": ev.get("people", ""), "what_we_discussed": ev.get("notes", "")}),
                       "sent": False}
    store.save("jobs", jobs)
    return job


# ---------- Offers ----------

OFFER_FIELDS = ("pay", "pay_unit", "bonus", "location", "start", "end", "deadline", "housing", "return_offer", "notes")


def save_offer(job_id, data):
    jobs = _jobs()
    job = _find(jobs, job_id)
    offer = {**(job.get("offer") or {})}
    for k in OFFER_FIELDS:
        if k in data and data[k] is not None:
            offer[k] = _clean_date(data[k]) if k in ("start", "end", "deadline") else str(data[k])[:500]
    first = not job.get("offer")
    job["offer"] = offer
    _advance(job, "Offer")
    if first:
        memory.log("offer", f"Offer from {job.get('company')}" + (f", decide by {offer.get('deadline')}" if offer.get("deadline") else ""), job["id"])
        memory.add_facts([f"Has an internship offer from {job.get('company')}" + (f" ({job.get('title')})" if job.get("title") else "")
                          + (f", deadline {offer['deadline']}" if offer.get("deadline") else "")], "offers")
    store.save("jobs", jobs)
    return job


def offer_draft(job_id, kind, notes):
    if kind not in ("extension", "negotiate", "accept", "decline"):
        raise llm.LLMError("Unknown offer message.")
    jobs = _jobs()
    job = _find(jobs, job_id)
    others = [{"company": j.get("company"), "pay": (j.get("offer") or {}).get("pay"), "pay_unit": (j.get("offer") or {}).get("pay_unit"),
               "deadline": (j.get("offer") or {}).get("deadline")} for j in jobs if j.get("offer") and j["id"] != job_id]
    in_process = [j.get("company") for j in jobs if j.get("status") in ("Online assessment", "Interviewing") and j["id"] != job_id]
    d = draft(kind, {"company": job.get("company"), "role": job.get("title"), "offer": job.get("offer") or {},
                     "recruiter": (job.get("contact") or {}).get("name", ""), "other_offers": others,
                     "other_processes_in_progress": in_process, "candidate_notes": notes})
    job.setdefault("offer", {})["draft"] = d
    store.save("jobs", jobs)
    return job


# ---------- Networking ----------

def load_network():
    return store.load("network", [])


def _person(people, pid):
    p = next((x for x in people if x["id"] == pid), None)
    if not p:
        raise llm.LLMError("Contact not found.")
    return p


PERSON_FIELDS = ("name", "title", "company", "how", "linkedin", "email", "status", "next_followup", "notes", "job_id", "school")


def add_person(data):
    if not str(data.get("name") or "").strip():
        raise llm.LLMError("Add the person's name.")
    p = {"id": uuid.uuid4().hex[:8], "created_at": datetime.now().isoformat(timespec="seconds"), "status": "To reach out",
         "chats": [], "last_contact": "", "next_followup": "", "how": "Alumni"}
    for k in PERSON_FIELDS:
        if data.get(k) not in (None, ""):
            p[k] = _clean_date(data[k]) if k == "next_followup" else str(data[k]).strip()[:500]
    people = load_network()
    people.insert(0, p)
    store.save("network", people)
    memory.log("network", f"Added {p['name']} ({p.get('company', '')}) to your network list")
    return people


def update_person(pid, data):
    people = load_network()
    p = _person(people, pid)
    for k in PERSON_FIELDS:
        if k in data and data[k] is not None:
            p[k] = _clean_date(data[k]) if k == "next_followup" else str(data[k]).strip()[:2000]
    if data.get("status") == "Messaged":
        p["last_contact"] = _today()
        p.setdefault("next_followup", "")
        if not p["next_followup"]:
            p["next_followup"] = (date.today() + timedelta(days=7)).isoformat()
    store.save("network", people)
    return people


def delete_person(pid):
    people = [p for p in load_network() if p["id"] != pid]
    store.save("network", people)
    return people


def log_chat(pid, when, notes):
    people = load_network()
    p = _person(people, pid)
    when = _clean_date(when) or _today()
    p.setdefault("chats", []).append({"date": when, "notes": str(notes or "")[:3000]})
    p["status"], p["last_contact"] = "Chatted", when
    p["next_followup"] = (date.fromisoformat(when) + timedelta(days=1)).isoformat()  # thank them within a day
    memory.log("network", f"Had a chat with {p['name']} ({p.get('company', '')})")
    store.save("network", people)
    return people


def person_draft(pid, kind, notes):
    if kind not in ("coffee_chat", "follow_up", "chat_thanks", "referral"):
        raise llm.LLMError("Unknown message type.")
    people = load_network()
    p = _person(people, pid)
    job = next((j for j in _jobs() if j["id"] == p.get("job_id")), None)
    p["draft"] = draft(kind, {"person": {k: p.get(k) for k in ("name", "title", "company", "how", "school")},
                              "past_chats": p.get("chats", [])[-2:], "candidate_notes": notes,
                              "role_of_interest": {"company": job.get("company"), "title": job.get("title"), "url": job.get("url")} if job else None})
    store.save("network", people)
    return people


def chat_prep(pid):
    people = load_network()
    p = _person(people, pid)
    out = llm.chat_json([
        {"role": "system", "content": prompts.CHAT_PREP_SYSTEM},
        {"role": "user", "content": json.dumps({"person": {k: p.get(k) for k in ("name", "title", "company", "how", "school", "notes")},
                                                "candidate": _candidate()}, ensure_ascii=False)},
    ], max_tokens=3000, temperature=0.5, timeout=120)
    p["prep"] = {"intro": out.get("intro", ""), "questions": out.get("questions") or [], "avoid": out.get("avoid") or [], "made": _today()}
    store.save("network", people)
    return people


# ---------- One calendar ----------

def calendar(days=120):
    """Everything with a date: deadlines, events, follow-ups, thank-yous due and offer deadlines."""
    today = date.today()
    horizon = (today + timedelta(days=days)).isoformat()
    items = []

    def add(d, kind, title, job=None, person=None, ref=None, time="", done=False):
        if d and d <= horizon and not done:
            items.append({"date": d, "time": time, "kind": kind, "title": title, "overdue": d < today.isoformat(),
                          "job_id": job["id"] if job else None, "company": (job or person or {}).get("company", ""),
                          "person_id": person["id"] if person else None, "ref": ref})

    for j in _jobs():
        closed = j.get("status") in ("Rejected", "Withdrawn", "Accepted")
        if j.get("deadline") and j.get("status") in ("Saved", "Ready", "Contacted", "Followed up"):
            add(j["deadline"], "Application deadline", f"Apply: {j.get('title') or j.get('company')}", j)
        if j.get("followup_due") and j.get("status") == "Contacted":
            add(j["followup_due"], "Follow-up", f"Follow up with {(j.get('contact') or {}).get('name') or j.get('company')}", j)
        for e in j.get("events") or []:
            if closed:
                continue
            label = EVENT_TYPES.get(e["type"], "Event")
            if e["type"] == "oa" and e.get("platform"):
                label = OA_PLATFORMS[e["platform"]]["label"]
            add(e["date"], EVENT_TYPES.get(e["type"], "Event"), f"{label}{': ' + e['title'] if e.get('title') else ''}", j,
                ref=e["id"], time=e.get("time", ""), done=e.get("done") or e["date"] < today.isoformat())
            if e["type"] == "interview" and e["date"] <= today.isoformat() and not (e.get("thank_you") or {}).get("sent"):
                add((date.fromisoformat(e["date"]) + timedelta(days=1)).isoformat(), "Thank-you note",
                    f"Send a thank-you for the {e.get('title') or 'interview'}", j, ref=e["id"])
        offer = j.get("offer") or {}
        if offer.get("deadline") and j.get("status") == "Offer":
            add(offer["deadline"], "Offer deadline", f"Decide on the {j.get('company')} offer", j)
    for p in load_network():
        if p.get("next_followup") and p.get("status") not in ("Referred",):
            what = "Thank" if p.get("status") == "Chatted" else "Follow up with"
            add(p["next_followup"], "Networking", f"{what} {p['name']}" + (f" ({p.get('company')})" if p.get("company") else ""), person=p)
    items.sort(key=lambda x: (x["date"], x["time"] or "99"))
    return items


def ics(items):
    """An .ics file the user can import into Google Calendar, Outlook or Apple Calendar."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Job Agent//Recruiting calendar//EN", "CALSCALE:GREGORIAN"]
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    for n, it in enumerate(items):
        d = it["date"].replace("-", "")
        text = f"{it['kind']}: {it['title']}" + (f" ({it['company']})" if it["company"] and it["company"] not in it["title"] else "")
        text = text.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;")
        lines += ["BEGIN:VEVENT", f"UID:{d}-{n}-{it.get('ref') or it.get('job_id') or it.get('person_id')}@job-agent", f"DTSTAMP:{stamp}"]
        if it.get("time"):
            hhmm = it["time"].replace(":", "")
            lines += [f"DTSTART:{d}T{hhmm}00", f"DURATION:PT1H"]
        else:
            nxt = (date.fromisoformat(it["date"]) + timedelta(days=1)).isoformat().replace("-", "")
            lines += [f"DTSTART;VALUE=DATE:{d}", f"DTEND;VALUE=DATE:{nxt}"]
        lines += [f"SUMMARY:{text}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def summary_for_mentors():
    """Compact pipeline facts for the lead mentor's daily plan."""
    items = calendar(21)
    jobs = _jobs()
    return {"next_3_weeks": [{k: it[k] for k in ("date", "kind", "title", "company", "job_id", "overdue")} for it in items[:15]],
            "offers": [{"job_id": j["id"], "company": j.get("company"), "deadline": (j.get("offer") or {}).get("deadline", ""),
                        "pay": (j.get("offer") or {}).get("pay", ""), "pay_unit": (j.get("offer") or {}).get("pay_unit", "")}
                       for j in jobs if j.get("offer") and j.get("status") == "Offer"],
            "network": {"total": len(load_network()),
                        "by_status": {s: sum(1 for p in load_network() if p.get("status") == s) for s in NETWORK_STATUSES}}}
