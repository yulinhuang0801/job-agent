"""Find more company job boards automatically (part of the daily run; also Find → Data sources).

Where new companies come from, cheapest first:
1. Links already in hand: job-board links in My jobs, alert emails, Dice and other postings.
2. Hacker News "Who is hiring?": job-board links in this month's thread (public Algolia API, no AI).
3. Name probes: companies from My jobs, alert emails and the Find lists that have no board yet, tried as
   Greenhouse, Lever and Ashby board names on their public APIs. A hit only counts if the board names the
   company (Greenhouse) or its postings mention it (Lever, Ashby), so a same-named stranger isn't added.
4. Targeted search (Serper or Tavily, a few queries a day, rotating): site:jobs.ashbyhq.com "data" intern.
5. SEC firms in the student's target industries: their own site, robots.txt-checked, scanned for a
   careers link and then for job-board links.

Everything found goes into the companies table, so the job-board crawl reads it from then on. New boards
are read right away. Each company is probed once (the probes table), so later runs only spend on new names.
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from . import crawlers, db, dice, official, services, store, taxonomy

NAME = "Board discovery"
SEARCH_DOMAINS = ["jobs.ashbyhq.com", "job-boards.greenhouse.io", "jobs.lever.co", "myworkdayjobs.com", "boards.greenhouse.io", "recruitee.com"]
MAX_PROBES = 40  # company names tried per run (up to 6 small API requests each)
MAX_FIRMS = 15  # SEC firm websites scanned per run (2 pages each)
READ_NOW = 80  # new boards read right away; the rest wait for the next job-board crawl
CAREERS = re.compile(r"career|jobs|join (us|our team)|work (with|at) us|open (roles|positions)|hiring|internship", re.I)
_SUFFIX = re.compile(r"\b(inc|llc|l\.?l\.?c|ltd|corp|corporation|co|company|holdings|group|technologies|technology|labs|lp|plc|gmbh|the)\b\.?", re.I)
# Plain words that are also somebody's board name; never probe these alone.
_COMMON = {"apple", "delta", "target", "general", "united", "american", "national", "global", "first", "capital", "partners", "health",
           "energy", "systems", "solutions", "services", "ventures", "management", "consulting", "digital", "data", "software"}
# Profile industry words → taxonomy industries (for choosing SEC firms to scan).
_INDUSTRY_WORDS = [
    (r"tech|software|internet|saas", ["Software & Internet", "AI & Machine Learning", "Fintech", "Hardware & Semiconductors"]),
    (r"\bai\b|machine learning|\bml\b", ["AI & Machine Learning"]), (r"fintech", ["Fintech"]),
    (r"invest|banking|\bib\b|m&a", ["Investment Banking", "Banking & Lending"]), (r"private equity|\bpe\b|buyout", ["Private Equity"]),
    (r"venture|\bvc\b", ["Venture Capital"]), (r"hedge|asset manag|quant", ["Hedge Funds & Asset Management"]),
    (r"wealth", ["Wealth Management"]), (r"real estate", ["Real Estate"]), (r"consult", ["Consulting"]),
    (r"biotech|pharma|life science", ["Biotech & Pharma"]), (r"health|medical|hospital", ["Healthcare", "Medical Devices"]),
    (r"energy|climate", ["Energy & Climate"]), (r"aerospace|defense", ["Aerospace & Defense"]),
    (r"consumer|retail|cpg", ["Consumer & Retail"]), (r"media|entertainment", ["Media & Entertainment"]), (r"gaming|games", ["Gaming"]),
]


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _state():
    return store.load("discover", {"cursor": 0, "runs": []})


def _known():
    with db.connect() as con:
        return {(r["board"], r["token"]) for r in con.execute("SELECT board, token FROM companies")}


def _boards_in(text):
    out = set()
    for url in re.findall(r"https?://[^\s\"'<>)\]]+", text or ""):
        board, token = crawlers.board_from_url(url)
        if board:
            out.add((board, token))
    return out


# ---------- 1. Links already in hand ----------

def from_links():
    found = set()
    for j in store.load("jobs", []):
        found |= _boards_in(" ".join(str(j.get(k) or "") for k in ("url", "apply_url", "website", "description")))
    with db.connect() as con:
        for r in con.execute("SELECT url, description FROM postings WHERE source NOT IN ('Company job boards')"):
            found |= _boards_in((r["url"] or "") + " " + (r["description"] or "")[:3000])
    return found


# ---------- 2. Hacker News "Who is hiring?" ----------

def from_hacker_news():
    try:
        stories = httpx.get("https://hn.algolia.com/api/v1/search_by_date",
                            params={"tags": "story,author_whoishiring", "hitsPerPage": 6}, timeout=20).json()["hits"]
        story = next(s for s in stories if "who is hiring" in s["title"].lower())
        found = set()
        for page in range(3):  # up to 3 × 200 top-level comments
            hits = httpx.get("https://hn.algolia.com/api/v1/search", timeout=20,
                             params={"tags": f"comment,story_{story['objectID']}", "hitsPerPage": 200, "page": page}).json().get("hits", [])
            for h in hits:
                found |= _boards_in(re.sub(r"&#x2F;", "/", h.get("comment_text") or ""))
            if len(hits) < 200:
                break
        return found
    except (httpx.HTTPError, StopIteration, KeyError, ValueError):
        return set()


# ---------- 3. Name probes ----------

def slugs(name):
    """Likely board names for a company: "Acme Robotics, Inc." → ["acmerobotics", "acme-robotics"]."""
    base = _SUFFIX.sub(" ", (name or "").lower().replace("&", " and "))
    words = re.findall(r"[a-z0-9]+", base)
    if not words:
        return []
    out = ["".join(words), "-".join(words)] if len(words) > 1 else [words[0]]
    if len(words) > 2:
        out.append("".join(words[:2]))  # "Jane Street Capital" → janestreet
    return [s for s in dict.fromkeys(out) if 3 <= len(s) <= 40 and s not in _COMMON]


def _mentions(posts, name):
    key = _norm(_SUFFIX.sub(" ", name))
    return bool(key) and any(key in _norm(p.get("title", "") + " " + p.get("description", "")) for p in posts[:20])


def probe(name, client=None):
    """(board, token) for a company's Greenhouse, Lever or Ashby board, or None."""
    http = client or httpx.Client(timeout=15, headers={"User-Agent": official.USER_AGENT})
    try:
        for slug in slugs(name):
            r = http.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
            if r.status_code == 200 and _norm(_SUFFIX.sub(" ", r.json().get("name", ""))) == _norm(_SUFFIX.sub(" ", name)):
                return "greenhouse", slug
            r = http.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json", "limit": 20})
            if r.status_code == 200 and isinstance(r.json(), list) and _mentions(
                    [{"title": "", "description": (p.get("descriptionPlain") or "") + (p.get("additionalPlain") or "")} for p in r.json()], name):
                return "lever", slug
            time.sleep(1)  # Lever asks for a 1-second crawl delay
            r = http.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
            if r.status_code == 200 and _mentions([{"title": "", "description": p.get("descriptionPlain") or ""}
                                                   for p in r.json().get("jobs", [])], name):
                return "ashby", slug
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        if client is None:
            http.close()
    return None


def _names_to_probe(limit):
    """Companies the student has seen (My jobs first, then alert emails, then other postings) without a board or a probe yet."""
    with db.connect() as con:
        have = {_norm(r["name"]) for r in con.execute("SELECT name FROM companies")}
        tried = {r["name"] for r in con.execute("SELECT name FROM probes")}
        alert = [r["company"] for r in con.execute("SELECT company, COUNT(*) n FROM postings WHERE board='email' GROUP BY company ORDER BY n DESC")]
        other = [r["company"] for r in con.execute(
            "SELECT company, COUNT(*) n FROM postings WHERE source NOT IN ('Company job boards', 'SimplifyJobs list') AND board != 'email' "
            "GROUP BY company ORDER BY n DESC LIMIT 300")]
        simplify = [r["company"] for r in con.execute("SELECT company, COUNT(*) n FROM postings WHERE source='SimplifyJobs list' "
                                                      "GROUP BY company ORDER BY n DESC LIMIT 500")]
    mine = [j.get("company", "") for j in store.load("jobs", [])]
    out = []
    for name in mine + alert + other + simplify:
        name = (name or "").strip()
        # Chinese or other non-Latin names can't be board names.
        if not name or not re.search(r"[A-Za-z]", name) or _norm(name) in have or name in tried or name in out:
            continue
        out.append(name)
        if len(out) >= limit:
            break
    return out


def probe_names(limit=MAX_PROBES, progress=None):
    names = _names_to_probe(limit)
    found, now = {}, datetime.now().isoformat(timespec="seconds")
    with httpx.Client(timeout=15, headers={"User-Agent": official.USER_AGENT}) as http:
        def one(name):
            return name, probe(name, http)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for i, (name, hit) in enumerate(pool.map(one, names)):
                if hit:
                    found[hit] = name
                if progress and i % 10 == 9:
                    progress(f"Tried {i + 1}/{len(names)} company names on Greenhouse, Lever and Ashby…")
    with db.connect() as con:
        con.executemany("INSERT OR REPLACE INTO probes VALUES (?,?,?,?)",
                        [(n, "name", "|".join(next((bt for bt, nm in found.items() if nm == n), ())) or "none", now) for n in names])
    return found, len(names)


# ---------- 4. Targeted search ----------

def _queries(profile):
    targets = profile.get("targets") or {}
    words = dice.keywords_from_targets(targets, limit=6) or ["software engineer"]
    level = "intern" if dice.wants_internships(targets) else "new grad"
    return [(f'site:{d} "{w}" {level}', d) for w in words for d in SEARCH_DOMAINS]


def from_search(budget, profile=None):
    """Up to `budget` searches, continuing where the last run stopped, so every role × board pair gets its turn."""
    if budget <= 0 or not (services.serper_enabled() or services.search_enabled()):
        return set(), 0
    qs = _queries(profile if profile is not None else store.load("profile", {}))
    st = _state()
    start = st.get("cursor", 0) % len(qs)
    picked = [qs[(start + i) % len(qs)] for i in range(min(budget, len(qs)))]
    found = set()
    for q, domain in picked:
        res = services.find_search({"query": q, "max_results": 10, "include_domains": [domain]})
        for r in res.get("results", []):
            found |= _boards_in(r.get("url", ""))
    st["cursor"] = (start + len(picked)) % len(qs)
    store.save("discover", st)
    return found, len(picked)


# ---------- 5. SEC firms' own careers pages ----------

def target_industries(profile):
    text = " ".join(str(x) for x in ((profile.get("targets") or {}).get("industries") or []) + ((profile.get("targets") or {}).get("roles") or []))
    out = []
    for pattern, names in _INDUSTRY_WORDS:
        if re.search(pattern, text, re.I):
            out += [n for n in names if n not in out]
    return out


def _careers_links(page_url, html):
    soup = BeautifulSoup(html, "html.parser")
    home = urlparse(page_url).netloc.removeprefix("www.")
    out = []
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"])
        host = urlparse(href).netloc.removeprefix("www.")
        if href.startswith("http") and (host == home or host.endswith("." + home)) and CAREERS.search(a.get_text(" ", strip=True) + " " + href):
            out.append(href.split("#")[0])
    return list(dict.fromkeys(out))[:2]


def scan_site(website):
    """Job-board links on a company's homepage or its careers page (robots.txt-checked)."""
    url = website if "://" in website else "https://" + website
    page = official.get_html(url)
    if not page:
        return set()
    found = _boards_in(page["html"])
    if not found:
        for link in _careers_links(page["url"], page["html"]):
            careers = official.get_html(link)
            if careers:
                found |= _boards_in(careers["html"] + " " + careers["url"])
            if found:
                break
    return found


def scan_firms(profile, limit=MAX_FIRMS, progress=None):
    industries = target_industries(profile)
    if not industries or limit <= 0:
        return {}, 0
    with db.connect() as con:
        tried = {r["name"] for r in con.execute("SELECT name FROM probes WHERE kind='site'")}
        marks = ",".join("?" * len(industries))
        firms = [dict(r) for r in con.execute(
            f"SELECT name, website FROM firms WHERE website != '' AND industry IN ({marks}) ORDER BY COALESCE(employees, 0) DESC LIMIT ?",
            (*industries, limit * 20))]
    firms = [f for f in firms if f["website"] not in tried][:limit]
    found, now = {}, datetime.now().isoformat(timespec="seconds")
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, (f, hits) in enumerate(pool.map(lambda f: (f, scan_site(f["website"])), firms)):
            for bt in hits:
                found[bt] = f["name"]
            if progress and i % 5 == 4:
                progress(f"Checked {i + 1}/{len(firms)} firm websites for job boards…")
    with db.connect() as con:
        con.executemany("INSERT OR REPLACE INTO probes VALUES (?,?,?,?)",
                        [(f["website"], "site", "found" if any(n == f["name"] for n in found.values()) else "none", now) for f in firms])
    return found, len(firms)


# ---------- The run ----------

def run(progress=None, searches=None, probes=MAX_PROBES, firms=MAX_FIRMS):
    """Find new boards, save them, and read their postings. Returns (new boards, message)."""
    say = progress or (lambda m: None)
    profile = store.load("profile", {})
    known = _known()
    finds = {}  # (board, token) → how it was found

    def add(pairs, via):
        for bt in pairs:
            if bt not in known and bt not in finds:
                finds[bt] = via
    say("Looking for job-board links in your jobs and alerts…")
    add(from_links(), "your jobs and alerts")
    say("Reading this month's Hacker News hiring thread…")
    add(from_hacker_news(), "Hacker News: Who is hiring?")
    say("Trying company names on Greenhouse, Lever and Ashby…")
    named, n_names = probe_names(probes, say)
    add(named, "company name")
    budget = int(searches if searches is not None else (store.get_settings().get("discover_searches") or 8))
    say("Searching job-board sites for your target roles…")
    searched, n_search = from_search(budget, profile)
    add(searched, "search")
    say("Checking firm websites in your target industries…")
    from_firms, n_firms = scan_firms(profile, firms, say)
    add(from_firms, "firm website")

    now = datetime.now().isoformat(timespec="seconds")
    # Boards the student is closest to first (their own jobs and alerts, then name hits, search, HN, firms).
    order = ["your jobs and alerts", "company name", "search", "Hacker News: Who is hiring?", "firm website"]
    ranked = sorted(finds, key=lambda bt: (order.index(finds[bt]), bt))
    now_read, later = ranked[:READ_NOW], ranked[READ_NOW:]
    say(f"Reading {len(now_read)} new job boards" + (f" ({len(later)} more wait for the next crawl)…" if later else "…"))

    def read(bt):
        try:
            return bt, crawlers._fetch_board(*bt), ""
        except (httpx.HTTPError, ValueError) as e:
            return bt, (None, []), e.__class__.__name__
    rows, companies = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for (board, token), (company, posts), err in pool.map(read, now_read):
            company = company or named.get((board, token)) or from_firms.get((board, token)) or token
            companies.append({"board": board, "token": token, "name": company, "industry": "", "website": "",
                              "jobs": len(posts), "status": err or "ok", "checked": now})
            rows += [crawlers._posting_row(p, company, board, "Company job boards") for p in posts]
    companies += [{"board": b, "token": t, "name": named.get((b, t)) or from_firms.get((b, t)) or t.split("|")[0], "industry": "",
                   "website": "", "jobs": 0, "status": "queued", "checked": ""} for b, t in later]
    with db.connect() as con:
        db.upsert(con, "companies", companies)
        for i in range(0, len(rows), 2000):
            db.upsert(con, "postings", rows[i:i + 2000])
        con.executemany("INSERT OR IGNORE INTO board_finds VALUES (?,?,?,?)", [(b, t, finds[(b, t)], now) for b, t in finds])
    st = _state()
    by_via = {}
    for v in finds.values():
        by_via[v] = by_via.get(v, 0) + 1
    st["runs"] = (st.get("runs", []) + [{"at": now, "new_boards": len(finds), "postings": len(rows), "by_source": by_via,
                                         "searches": n_search, "names_tried": n_names, "firms_checked": n_firms}])[-30:]
    store.save("discover", st)
    ok = sum(1 for c in companies if c["status"] == "ok")
    detail = ", ".join(f"{n} from {v}" for v, n in sorted(by_via.items(), key=lambda x: -x[1]))
    return len(finds), (f"{len(finds)} new company job boards ({detail}); {ok} read, {len(rows):,} postings added"
                        + (f"; {len(later)} more are read by the next job-board crawl" if later else "")
                        if finds else f"No new job boards this time ({n_search} searches, {n_firms} firm websites checked)")


def summary():
    with db.connect() as con:
        by_via = {r["via"]: r["n"] for r in con.execute("SELECT via, COUNT(*) n FROM board_finds GROUP BY via")}
        boards = con.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    st = _state()
    return {"boards": boards, "found_by": by_via, "last_run": (st.get("runs") or [None])[-1]}
