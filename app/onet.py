"""O*NET occupations: what people in a role actually do, for questions by occupation.

Downloads a few CSV files from the O*NET 31.0 Database (about 20 MB, once) into data/onet.sqlite:
occupations with descriptions and job zones, reported and alternate job titles (for matching a
role like "Equity Research Intern" to an occupation), core task statements, technologies used,
and knowledge areas ranked by importance.

Attribution (CC BY 4.0): This product includes information from the O*NET 31.0 Database by the
U.S. Department of Labor, Employment and Training Administration (USDOL/ETA). Used under the
CC BY 4.0 license. O*NET® is a trademark of USDOL/ETA. Job Agent has selected and reformatted
this information (titles, core tasks, technologies, top knowledge areas) and uses it to write
practice questions; USDOL/ETA has not approved, endorsed, or tested these modifications."""
import csv
import io
import json
import re
import sqlite3
from contextlib import contextmanager

import httpx

from . import llm, store

VERSION = "31.0"
BASE = "https://www.onetcenter.org/dl_files/database/db_31_0_csv/"
PATH = store.DATA_DIR / "onet.sqlite"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
ATTRIBUTION = ("Includes information from the O*NET 31.0 Database by the U.S. Department of Labor, Employment and Training "
               "Administration (USDOL/ETA), used under the CC BY 4.0 license. O*NET® is a trademark of USDOL/ETA. Job Agent has "
               "selected and reformatted this information to write practice questions; USDOL/ETA has not approved, endorsed, or "
               "tested these modifications.")
HEADERS = {"User-Agent": "JobAgent/1.0 (interview prep for students; +https://github.com/yulinhuang0801/job-agent)"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS occupations (code TEXT PRIMARY KEY, title TEXT, description TEXT, job_zone INTEGER);
CREATE TABLE IF NOT EXISTS titles (code TEXT, title TEXT);
CREATE INDEX IF NOT EXISTS titles_code ON titles(code);
CREATE TABLE IF NOT EXISTS tasks (code TEXT, task TEXT, core INTEGER);
CREATE INDEX IF NOT EXISTS tasks_code ON tasks(code);
CREATE TABLE IF NOT EXISTS tech (code TEXT, example TEXT, category TEXT, hot INTEGER);
CREATE INDEX IF NOT EXISTS tech_code ON tech(code);
CREATE TABLE IF NOT EXISTS knowledge (code TEXT, element TEXT, importance REAL);
CREATE INDEX IF NOT EXISTS knowledge_code ON knowledge(code);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class OnetError(Exception):
    pass


@contextmanager
def connect():
    store.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def ready():
    if not PATH.exists():
        return False
    with connect() as con:
        row = con.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()
        return bool(row)


def _rows(name, progress=None):
    if progress:
        progress(f"Downloading {name}…")
    r = httpx.get(BASE + name + ".csv", headers=HEADERS, timeout=httpx.Timeout(30, read=180), follow_redirects=True)
    r.raise_for_status()
    return csv.DictReader(io.StringIO(r.content.decode("utf-8-sig")))


def download(progress=None):
    """Fetch the files and rebuild data/onet.sqlite. Returns (occupations, message) for the data-source panel."""
    try:
        occ = list(_rows("occupation_data", progress))
        zones = {r["O*NET-SOC Code"]: int(r["Job Zone"]) for r in _rows("job_zones", progress) if r.get("Job Zone", "").isdigit()}
        titles = [(r["O*NET-SOC Code"], r["Reported Job Title"]) for r in _rows("sample_of_reported_titles", progress)]
        titles += [(r["O*NET-SOC Code"], r["Job Title"]) for r in _rows("job_titles", progress)]
        tasks = [(r["O*NET-SOC Code"], r["Task"], int(r.get("Task Type") == "Core")) for r in _rows("task_statements", progress)]
        tech = [(r["O*NET-SOC Code"], r["Workplace Example"], r["Element Name"], int(r.get("Hot Technology") == "Y"))
                for r in _rows("software_skills", progress)]
        know = [(r["O*NET-SOC Code"], r["Element Name"], float(r["Data Value"] or 0))
                for r in _rows("knowledge", progress) if r.get("Scale ID") == "IM"]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise OnetError(f"Couldn't download O*NET ({e.__class__.__name__}: {e})")
    if progress:
        progress("Saving…")
    with connect() as con:
        for t in ("occupations", "titles", "tasks", "tech", "knowledge", "meta"):
            con.execute(f"DELETE FROM {t}")
        con.executemany("INSERT OR REPLACE INTO occupations VALUES (?,?,?,?)",
                        [(r["O*NET-SOC Code"], r["Title"], r["Description"], zones.get(r["O*NET-SOC Code"])) for r in occ])
        con.executemany("INSERT INTO titles VALUES (?,?)", titles)
        con.executemany("INSERT INTO tasks VALUES (?,?,?)", tasks)
        con.executemany("INSERT INTO tech VALUES (?,?,?,?)", tech)
        con.executemany("INSERT INTO knowledge VALUES (?,?,?)", know)
        con.execute("INSERT OR REPLACE INTO meta VALUES ('version', ?)", (VERSION,))
    global _title_index
    _title_index = None
    return len(occ), f"{len(occ):,} occupations, {len(tasks):,} tasks, {len(tech):,} technologies (O*NET {VERSION})"


# ---------- Matching a role to occupations ----------

_title_index = None
STOP = {"intern", "internship", "summer", "analyst", "associate", "junior", "senior", "entry", "level", "the", "and", "of", "for",
        "in", "a", "an", "i", "ii", "iii", "co", "op", "coop", "student", "trainee", "program", "2026", "2027", "2028", "all", "other",
        "except", "related", "workers", "specialist", "specialists", "position", "role", "team", "remote", "unpaid", "paid", "part",
        "time", "full", "hybrid", "onsite", "tech", "technology", "job", "jobs", "opportunity", "early", "career"}
# Student-role phrases that O*NET words differently (applied before splitting into words).
PHRASES = [
    (r"\bprivate equity\b|\bsearch fund\b|\bventure capital\b|\bequity research\b|\binvestment bank\w*\b|\b(ib|pe|vc)\b", " financial investment analysts "),
    (r"\bprotein\w*|\bbiotech\w*|\bmolecular biolog\w*|\bbiochem\w*", " biochemists biophysicists "),
    (r"\bbrand\b|\bgrowth marketing\b|\bdigital marketing\b", " marketing specialists "),
    (r"\b(swe|sde)\b|\bsoftware engineer\w*", " software developers "),
    (r"\bux\b|\bui\b|\buser experience\b|\bproduct design\w*", " web digital interface designers "),
    (r"\b(ml|ai)\b|\bmachine learning\b", " machine learning engineers data scientists "),
    (r"\bpm\b|\bproduct manag\w*", " marketing managers "),  # O*NET files product managers under Marketing Managers
    (r"\bnurs(e|ing)\b", " registered nurses "),
    (r"\bconsult\w*", " management analysts "),
    (r"\bassurance\b|\baudit\w*", " accountants auditors "),
]
PENALTY = [(r"teachers?, postsecondary|teachers?\b", r"teach|professor|instructor|tutor"),
           (r"\bmanagers?\b|\bchief\b|\bexecutives?\b|\bdirectors?\b", r"manag|chief|director|executive|lead")]


def _tokens(text):
    t = " " + (text or "").lower() + " "
    for pattern, repl in PHRASES:
        t = re.sub(pattern, repl, t)
    return [w for w in re.findall(r"[a-z0-9+#]+", t) if w not in STOP and len(w) > 1]


def _index():
    global _title_index
    if _title_index is None:
        import math
        with connect() as con:
            occ = {r["code"]: dict(r) for r in con.execute("SELECT code, title, job_zone FROM occupations")}
            rows = [(r["code"], r["title"], False) for r in con.execute("SELECT code, title FROM titles")]
        rows += [(c, o["title"], True) for c, o in occ.items()]
        df = {}
        entries = []
        for code, title, main in rows:
            toks = set(re.findall(r"[a-z0-9+#]+", title.lower())) - STOP
            entries.append((code, title, toks, main))
            for w in toks:
                df[w] = df.get(w, 0) + 1
        n = len(entries)
        idf = {w: math.log(n / (1 + c)) for w, c in df.items()}
        _title_index = (occ, entries, idf)
    return _title_index


def match(text, limit=5):
    """Occupations whose titles best match a role or major, best first. Rare words weigh more; an
    occupation's own title beats an alternate title; managers and professors only when asked for."""
    if not ready():
        return []
    want = set(_tokens(text))
    if not want:
        return []
    occ, rows, idf = _index()
    total = sum(idf.get(w, 1.0) for w in want) or 1.0
    query = (text or "").lower()
    best = {}
    for code, title, toks, main in rows:
        hit = want & toks
        if not hit:
            continue
        cover = sum(idf.get(w, 1.0) for w in hit) / total
        tight = len(hit) / len(toks) if toks else 0
        score = cover + 0.3 * tight + (0.1 if main else 0)
        low = occ[code]["title"].lower()
        for pattern, unless in PENALTY:
            if re.search(pattern, low) and not re.search(unless, query):
                score -= 0.5
        if score > best.get(code, (-9, ""))[0]:
            best[code] = (score, title)
    ranked = sorted(best.items(), key=lambda kv: -kv[1][0])[:limit]
    return [{"code": c, "title": occ[c]["title"], "matched_title": t, "score": round(s, 2), "job_zone": occ[c]["job_zone"]}
            for c, (s, t) in ranked if c in occ]


def best_match(text):
    m = match(text, 1)
    return m[0] if m and m[0]["score"] >= 0.7 else None


def profile(code):
    with connect() as con:
        o = con.execute("SELECT * FROM occupations WHERE code = ?", (code,)).fetchone()
        if not o:
            raise OnetError(f"No O*NET occupation {code}.")
        tasks = [r["task"] for r in con.execute("SELECT task FROM tasks WHERE code = ? ORDER BY core DESC, rowid LIMIT 12", (code,))]
        tech = [r["example"] for r in con.execute("SELECT example FROM tech WHERE code = ? ORDER BY hot DESC, rowid LIMIT 12", (code,))]
        know = [r["element"] for r in con.execute("SELECT element FROM knowledge WHERE code = ? ORDER BY importance DESC LIMIT 8", (code,))]
    return {"code": code, "title": o["title"], "description": o["description"], "job_zone": o["job_zone"],
            "tasks": tasks, "technologies": tech, "knowledge": know,
            "url": f"https://www.onetonline.org/link/summary/{code}"}


def material(code):
    p = profile(code)
    return (f"OCCUPATION PROFILE (O*NET {VERSION}, {p['code']} {p['title']}): {p['description']}\n"
            f"Core tasks: " + "; ".join(p["tasks"][:8]) + "\nTechnologies: " + ", ".join(p["technologies"][:10])
            + "\nMost important knowledge: " + ", ".join(p["knowledge"][:6]))


# ---------- Questions written from an occupation ----------

ONET_QUESTIONS_SYSTEM = """You write technical interview questions for a college student applying to an internship or entry-level job in one occupation. Output a json object only.

You get the occupation's real core tasks, technologies and most important knowledge areas (from O*NET), and the candidate's background. Write {n} questions that an interviewer for an INTERN in this occupation would plausibly ask, each tied to one listed task or knowledge area:
- Mix: mostly level 1-2 (what a strong student knows), one level 3. Include a calculation or worked problem (type "problem") when the field uses numbers, a short coding task (type "practical", with lang) only for software or data occupations, and a scenario ("design") when the job is about judgment or process.
- Ask about fundamentals and how the work is done, not trivia about the O*NET database or a specific product's menu.
- rubric: what a strong answer includes, specific enough to grade fairly.
- based_on: the task, technology or knowledge area the question comes from (copied from the list).

JSON shape:
{{"questions": [{{"level": 1, "type": "<concept|problem|practical|design>", "question": "", "rubric": "", "based_on": "", "lang": "<only for practical>"}}]}}"""


def _cache_path(code):
    d = store.DATA_DIR / "onet_questions"
    d.mkdir(parents=True, exist_ok=True)
    return d / (code.replace(".", "_") + ".json")


def questions(code, n=4, background=""):
    """Practice questions for an occupation: unused cached ones first, then new ones from the AI."""
    path = _cache_path(code)
    cached = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    used = set(store.load("qlib_used", {}).get("onet", []))
    fresh = [q for q in cached if q["id"] not in used]
    if len(fresh) < n:
        p = profile(code)
        out = llm.chat_json([
            {"role": "system", "content": ONET_QUESTIONS_SYSTEM.format(n=max(n, 5))},
            {"role": "user", "content": json.dumps({"occupation": p, "candidate": background[:2500]}, ensure_ascii=False)},
        ], max_tokens=6000, temperature=0.5, timeout=150)
        start = len(cached)
        for i, q in enumerate(out.get("questions") or []):
            if q.get("question") and q.get("rubric"):
                item = {"id": f"onet_{code.replace('.', '_')}_{start + i + 1}", "area": f"O*NET · {p['title']}",
                        "level": max(1, min(3, int(q.get("level") or 2))), "type": q.get("type") if q.get("type") in ("concept", "problem", "practical", "design") else "concept",
                        "question": q["question"], "rubric": q["rubric"] + (f"\n(Based on the O*NET {VERSION} task or skill: {q.get('based_on')})" if q.get("based_on") else ""),
                        "source": "O*NET", "link": p["url"], "ai_written": True}
                if item["type"] == "practical":
                    item["lang"] = q.get("lang") or "python"
                cached.append(item)
                fresh.append(item)
        path.write_text(json.dumps(cached, ensure_ascii=False, indent=1), encoding="utf-8")
    picked = fresh[:n]
    store.save("qlib_used", {**store.load("qlib_used", {}), "onet": list(used | {q["id"] for q in picked})[-2000:]})
    return picked


def areas_for(profile_data):
    """Occupation areas for the Skills check, from the candidate's target roles (best match per role)."""
    if not ready():
        return []
    roles = (profile_data.get("targets") or {}).get("roles") or []
    if isinstance(roles, str):
        roles = [roles]
    out, seen = [], set()
    # A role like "tech internship (exploring software engineering, data/ML, web/frontend)" names several
    # fields: match each part on its own.
    parts = []
    for role in roles[:4]:
        parts += [p.strip() for p in re.split(r"[(),;/]|\band\b|\bor\b", str(role)) if len(p.strip()) > 1]
    for part in parts[:12]:
        m = best_match(part)
        if m and m["code"] not in seen and len(out) < 4:
            seen.add(m["code"])
            out.append({"id": f"onet:{m['code']}", "label": f"O*NET · {m['title']}", "source": "O*NET", "count": None,
                        "matched": part})
    for code in store.load("onet_picked", []):
        if code not in seen:
            try:
                seen.add(code)
                out.append({"id": f"onet:{code}", "label": f"O*NET · {profile(code)['title']}", "source": "O*NET", "count": None})
            except OnetError:
                pass
    return out
