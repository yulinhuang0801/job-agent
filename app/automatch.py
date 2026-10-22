"""Daily matches: new postings scored against the profile, the best ones prepared before the student looks.

1. Prefilter the local job database (no AI): the student's level (internship / new grad), the functions
   their target roles map to, remote-only when that's all they asked for, and no "US citizens only" roles
   for international students. Postings already scored, or already in My jobs, are skipped.
2. Rank what's left by a cheap keyword score (title words vs target roles and skills, recency, remote).
3. The AI scores the top ~30 (batches of 10) with a one-line reason and any concern.
4. The top few above the bar are added to My jobs and prepared: fit check, tailored resume and cover
   letter, and (on "full") company and contact research. How many and how deep is a setting.
Results are in data/matches.json; Home shows them and the lead mentor plans around them.
"""
import json
import re
from datetime import date, datetime, timedelta

from . import db, dice, llm, memory, prompts, store, taxonomy

NAME = "Daily matches"
POOL = 600  # newest prefiltered postings considered
TO_SCORE = 30  # sent to the AI each run
BATCH = 10
KEEP_DAYS = 7  # unanswered matches stay on Home this long
DEPTHS = {"fit": "fit check only", "docs": "fit check, tailored resume and cover letter", "full": "fit, documents and contact research"}
_INTERNATIONAL = re.compile(r"f-?1|j-?1|cpt|opt|visa|sponsor|international|h-?1b", re.I)
_DOMESTIC = re.compile(r"citizen|green card|permanent resident|\bgc\b|no sponsorship needed", re.I)
_STOP = {"and", "or", "the", "of", "for", "in", "a", "to", "with", "tech", "intern", "internship", "role", "roles"}


def load():
    return store.load("matches", {"date": "", "items": [], "seen": {}, "history": [], "stats": {}})


def settings():
    s = store.get_settings()
    return {"on": s.get("automatch", "on") == "on", "prepare": max(0, min(10, int(s.get("automatch_prepare") or 3))),
            "depth": s.get("automatch_depth") if s.get("automatch_depth") in DEPTHS else "docs",
            "min_score": max(0, min(100, int(s.get("automatch_min") or 70)))}


# ---------- 1-2. Prefilter and rank (no AI) ----------

def preferences(profile):
    t = profile.get("targets") or {}
    keywords = dice.keywords_from_targets(t, limit=10)
    functions = sorted({taxonomy.classify_function(k) for k in keywords} - {"Other"})
    locations = [str(x) for x in t.get("locations") or []]
    auth = str(t.get("work_authorization") or "")
    tokens = {w for k in keywords for w in re.findall(r"[a-z0-9+#]+", k.lower()) if w not in _STOP and len(w) > 1}
    skills = {str(s.get("name") if isinstance(s, dict) else s).lower() for s in ((profile.get("skills") or {}).get("technical") or [])}
    return {"keywords": keywords, "functions": functions, "interns": dice.wants_internships(t),
            "remote_only": bool(locations) and all(re.search(r"remote|anywhere", x, re.I) for x in locations),
            "locations": locations, "international": bool(_INTERNATIONAL.search(auth)) and not _DOMESTIC.search(auth),
            "tokens": tokens, "skills": {s for s in skills if s}}


def _query(prefs, remote):
    where, args = [], []
    types = ["Internship", "Co-op"] if prefs["interns"] else ["New grad / Entry level"]
    where.append(f"job_type IN ({','.join('?' * len(types))})")
    args += types
    if prefs["functions"]:
        where.append(f"function IN ({','.join('?' * len(prefs['functions']))})")
        args += prefs["functions"]
    if remote:
        where.append("remote = 1")
    if prefs["international"]:
        where.append("COALESCE(sponsorship, '') != 'US citizens only'")
    return ("SELECT id, source, board, company, title, department, function, job_type, term, location, remote, url, posted, "
            f"sponsorship, substr(description, 1, 900) AS description FROM postings WHERE {' AND '.join(where)} "
            "ORDER BY (posted = '') , posted DESC LIMIT ?", args + [POOL])


def _rank(row, prefs):
    title = f"{row['title']} {row['department']}".lower()
    score = 3 * sum(1 for w in prefs["tokens"] if re.search(r"\b" + re.escape(w) + r"\b", title))
    score += sum(1 for s in prefs["skills"] if len(s) > 1 and s in (row["description"] or "").lower())
    if row["remote"]:
        score += 2
    elif prefs["remote_only"]:
        score -= 4  # only here because there were too few remote roles
    if row["posted"]:
        age = (date.today() - date.fromisoformat(row["posted"][:10])).days if re.match(r"\d{4}-\d{2}-\d{2}", row["posted"]) else 60
        score += 3 if age <= 7 else 1 if age <= 30 else -2
    if re.search(r"senior|staff|principal|lead|manager|director|\bii\b|\biii\b", title):
        score -= 6
    return score


def candidates(profile, state=None):
    """(ranked postings not scored before, stats)."""
    state = state if state is not None else load()
    prefs = preferences(profile)
    seen = state.get("seen", {})
    jobs = store.load("jobs", [])
    have_urls = {j.get("url") for j in jobs if j.get("url")}
    have_pairs = {((j.get("company") or "").lower(), (j.get("title") or "").lower()) for j in jobs}
    with db.connect() as con:
        sql, args = _query(prefs, prefs["remote_only"])
        rows = [dict(r) for r in con.execute(sql, args)]
        relaxed = False
        if prefs["remote_only"] and len([r for r in rows if r["id"] not in seen]) < 10:
            sql, args = _query(prefs, False)  # too few remote roles: look at on-site ones too, ranked lower
            rows = [dict(r) for r in con.execute(sql, args)]
            relaxed = True
    out, pairs = [], set()
    for r in rows:
        pair = ((r["company"] or "").lower(), (r["title"] or "").lower())
        if r["id"] in seen or r["url"] in have_urls or pair in have_pairs or pair in pairs:
            continue
        pairs.add(pair)
        r["rank"] = _rank(r, prefs)
        out.append(r)
    out.sort(key=lambda r: -r["rank"])
    return out, {"pool": len(rows), "new": len(out), "relaxed_remote": relaxed, "functions": prefs["functions"],
                 "level": "internships" if prefs["interns"] else "new grad", "remote_only": prefs["remote_only"],
                 "international": prefs["international"]}


# ---------- 3. AI scoring ----------

def _profile_brief(profile):
    keep = {k: profile.get(k) for k in ("targets", "summary", "education")}
    keep["skills"] = profile.get("skills")
    keep["experience"] = [{"title": e.get("title"), "company": e.get("company")} for e in profile.get("experience") or []]
    keep["projects"] = [{"name": p.get("name"), "tech": p.get("tech")} for p in profile.get("projects") or []]
    return json.dumps(keep, ensure_ascii=False)[:5000]


def score(profile, rows):
    out = {}
    brief = _profile_brief(profile)
    facts = memory.shared_block(max_facts=15, max_events=0)
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        items = [{"id": r["id"], "title": r["title"], "company": r["company"], "location": r["location"] or ("Remote" if r["remote"] else ""),
                  "type": r["job_type"], "sponsorship_note": r["sponsorship"] or "", "description": (r["description"] or "")[:700]} for r in batch]
        try:
            res = llm.chat_json([
                {"role": "system", "content": prompts.MATCH_SYSTEM},
                {"role": "user", "content": f"STUDENT PROFILE:\n{brief}\n" + (f"\nNEWER FACTS:\n{facts}\n" if facts else "")
                                            + "\nPOSTINGS:\n" + json.dumps(items, ensure_ascii=False)},
            ], max_tokens=3000, temperature=0, think=False, timeout=120)
        except llm.LLMError:
            continue
        for m in res.get("matches") or []:
            if m.get("id") in {r["id"] for r in batch}:
                try:
                    out[m["id"]] = {"score": max(0, min(100, int(m.get("score") or 0))), "why": str(m.get("why") or "")[:300],
                                    "concerns": str(m.get("concerns") or "")[:200]}
                except (TypeError, ValueError):
                    continue
    return out


# ---------- The run ----------

def run(prepare=None, progress=None, force=False):
    """Score today's new postings and prepare the best. `prepare(item, depth)` adds one to My jobs and returns
    {"job_id", "done": [steps], "error"}; main.py passes it in. Returns (matches, message)."""
    say = progress or (lambda m: None)
    profile = store.load("profile", {})
    if not profile:
        return 0, "Build your profile first (About you), so matches have something to compare against."
    state = load()
    today = date.today().isoformat()
    if state.get("date") == today and not force:
        return len(today_items(state)), "Today's matches are already in."
    cfg = settings()
    say("Picking postings that fit your level, roles and location…")
    rows, stats = candidates(profile, state)
    top = rows[:TO_SCORE]
    say(f"Scoring {len(top)} of {stats['new']:,} new postings against your profile…")
    scores = score(profile, top)
    now = datetime.now().isoformat(timespec="seconds")
    new = []
    for r in top:
        if r["id"] not in scores:
            continue  # the AI call failed for this batch; try it again next run
        state.setdefault("seen", {})[r["id"]] = today
        s = scores[r["id"]]
        new.append({"id": r["id"], "date": today, "title": r["title"], "company": r["company"], "location": r["location"],
                    "remote": bool(r["remote"]), "url": r["url"], "posted": r["posted"], "source": r["source"], "job_type": r["job_type"],
                    "sponsorship": r["sponsorship"] or "", "description": r["description"] or "",
                    "score": s["score"], "why": s["why"], "concerns": s["concerns"], "status": "new", "job_id": "", "prepared": []})
    new.sort(key=lambda m: -m["score"])

    prepared = 0
    if prepare and cfg["prepare"]:
        for m in [m for m in new if m["score"] >= cfg["min_score"]][:cfg["prepare"]]:
            say(f"Preparing {m['company']}: {DEPTHS[cfg['depth']]}…")
            res = prepare(m, cfg["depth"]) or {}
            m["job_id"], m["prepared"], m["prepare_error"] = res.get("job_id", ""), res.get("done", []), res.get("error", "")
            if m["job_id"]:
                m["status"] = "added"
                prepared += 1

    cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
    fresh = {m["id"] for m in new}
    old = [m for m in state.get("items", []) if m["date"] >= cutoff and m["id"] not in fresh and m["status"] in ("new", "added")]
    state["items"] = sorted(new + old, key=lambda m: (m["date"] != today, -m["score"]))[:60]
    state["seen"] = {k: v for k, v in state.get("seen", {}).items() if v >= (date.today() - timedelta(days=90)).isoformat()}
    strong = sum(1 for m in new if m["score"] >= cfg["min_score"])
    msg = (f"{len(new)} new postings scored, {strong} strong fits (≥{cfg['min_score']}), {prepared} prepared for you"
           if new else "No new postings matched your level and roles today. Try widening your target roles, or refresh the job boards.")
    if new:
        memory.log("job", f"Daily matches: {len(new)} new postings scored, {strong} strong fits, {prepared} prepared"
                   + (": " + ", ".join(m["company"] for m in new if m["job_id"]) if prepared else ""))
    if state.get("date") == today:  # a second run today adds to this morning's numbers
        before = state.get("stats", {})
        strong, prepared, scored = strong + before.get("strong", 0), prepared + before.get("prepared", 0), len(new) + before.get("scored", 0)
        state["history"] = [h for h in state.get("history", []) if h["date"] != today]
    else:
        scored = len(new)
    state.update({"date": today, "ran_at": now, "stats": {**stats, "scored": scored, "strong": strong, "prepared": prepared}})
    state["history"] = (state.get("history", []) + [{"date": today, "scored": scored, "strong": strong, "prepared": prepared}])[-60:]
    store.save("matches", state)
    return len(new), msg


def today_items(state=None):
    state = state if state is not None else load()
    return [m for m in state.get("items", []) if m["date"] == state.get("date")]


def set_status(pid, status, job_id=""):
    state = load()
    for m in state.get("items", []):
        if m["id"] == pid:
            m["status"] = status
            if job_id:
                m["job_id"] = job_id
            store.save("matches", state)
            return m
    return None


def public():
    state = load()
    cfg = settings()
    items = [{k: v for k, v in m.items() if k != "description"} for m in state.get("items", []) if m["status"] != "dismissed"]
    return {"date": state.get("date", ""), "ran_at": state.get("ran_at", ""), "items": items, "stats": state.get("stats", {}),
            "history": state.get("history", [])[-14:], "settings": {**cfg, "depth_label": DEPTHS[cfg["depth"]]}}


def summary_for_mentors():
    """What the lead mentor sees: today's strong matches and whether they've been acted on."""
    state = load()
    cfg = settings()
    items = [m for m in state.get("items", []) if m["status"] in ("new", "added")]
    return {"last_run": state.get("date", ""),
            "strong": [{"company": m["company"], "title": m["title"], "score": m["score"], "why": m["why"], "concerns": m["concerns"],
                        "job_id": m["job_id"], "prepared": m["prepared"], "date": m["date"]}
                       for m in items if m["score"] >= cfg["min_score"]][:8],
            "others_waiting": sum(1 for m in items if m["score"] < cfg["min_score"] and m["status"] == "new")}
