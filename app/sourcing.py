"""Find firms and job postings to reach out to.

Sources:
- Web search by category, the "keyword + location + industry" approach from the boutique internship
  guide. Uses Google results (Serper.dev) when that key is saved, otherwise Tavily.
- SimplifyJobs' internship lists on GitHub (summer and off-season), parsed directly, no AI needed.
- Hacker News "Who is hiring?" monthly thread, through the public Algolia API.
- The user's own sites and search phrases (Find tab → My sources).
- Lists the user imports (pasted names/URLs, or a CSV exported from Axial, Apollo, etc.).

LinkedIn is not crawled: it needs a login and its terms forbid automated scraping. The Find tab
links to LinkedIn's own search so the user can browse it themselves.
"""
import csv
import html
import io
import json
import re
import time
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from . import llm, prompts, services, store

CATEGORIES = {
    "search_funds": {
        "label": "Search funds & ETA", "kind": "firm", "source": "web",
        "queries": ["search fund {loc}", "search fund acquiring {industry} business", "self-funded searcher {industry} acquisition {loc}",
                    "entrepreneurship through acquisition search fund {loc}", "site:searchfunder.com search fund {industry}"],
    },
    "small_pe": {
        "label": "Small private equity", "kind": "firm", "source": "web",
        "queries": ["lower middle market private equity firm {loc}", "{industry} private equity firm {loc}",
                    "independent sponsor {industry} {loc}", "small private equity firm {industry} {loc}"],
    },
    "boutique_ib": {
        "label": "Boutique investment banks", "kind": "firm", "source": "web",
        "queries": ["boutique investment bank {loc}", "{industry} investment bank boutique {loc}",
                    "M&A advisory boutique {industry} {loc}", "lower middle market investment bank {loc}"],
    },
    "small_vc": {
        "label": "Small VC firms", "kind": "firm", "source": "web",
        "queries": ["early stage venture capital firm {loc}", "{industry} seed venture capital fund {loc}",
                    "micro VC fund {industry} {loc}"],
    },
    "boutique_consulting": {
        "label": "Boutique consulting", "kind": "firm", "source": "web",
        "queries": ["boutique consulting firm {loc}", "boutique strategy consulting firm {industry} {loc}",
                    "{industry} consulting boutique {loc}"],
    },
    "family_offices": {
        "label": "Family offices", "kind": "firm", "source": "web",
        "queries": ["single family office {loc} investments", "family office direct investments {industry} {loc}"],
    },
    "quant": {
        "label": "Quant & trading firms", "kind": "firm", "source": "web",
        "queries": ["quantitative trading firm internship {loc}", "proprietary trading firm {loc} intern",
                    "small hedge fund {industry} {loc} analyst intern"],
    },
    "tech_startups": {
        "label": "Tech startups (YC, Wellfound)", "kind": "posting", "source": "web",
        "queries": ["site:workatastartup.com {role} intern", "site:ycombinator.com/companies {industry} hiring intern",
                    "site:wellfound.com {role} intern {loc}", "seed stage startup {industry} hiring {role} intern {loc}"],
    },
    "postings": {
        "label": "Company job boards (Greenhouse, Lever, Ashby)", "kind": "posting", "source": "web",
        "queries": ["site:boards.greenhouse.io {role} intern {loc}", "site:job-boards.greenhouse.io {role} intern {loc}",
                    "site:jobs.lever.co {role} intern {loc}", "site:jobs.ashbyhq.com {role} intern {loc}"],
    },
    "simplify_summer": {
        "label": "Summer internships list (SimplifyJobs, tech & quant)", "kind": "posting", "source": "simplify",
        "file": "README.md",
    },
    "simplify_offseason": {
        "label": "Off-season & part-time internships (SimplifyJobs)", "kind": "posting", "source": "simplify",
        "file": "README-Off-Season.md",
    },
    "hn_hiring": {
        "label": "Hacker News: Who is hiring? (startups)", "kind": "posting", "source": "hn",
    },
    "my_sites": {
        "label": "My sites & searches", "kind": "firm", "source": "custom",
    },
}

SIMPLIFY_REPO = "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/"
BIG_FIRMS = re.compile(r"\b(goldman|morgan stanley|jpmorgan|j\.p\. morgan|citi(group)?|bank of america|moelis|evercore|lazard|"
                       r"centerview|pjt|perella|houlihan|jefferies|blackstone|kkr|carlyle|apollo global|bain|mckinsey|bcg|"
                       r"deloitte|pwc|kpmg|ernst|sequoia|andreessen|a16z|accel|tiger global)\b", re.I)
DEFAULT_SOURCES = {"sites": [], "queries": []}


def categories():
    return [{"id": k, "label": v["label"], "kind": v["kind"], "source": v["source"]} for k, v in CATEGORIES.items()]


def load_sources():
    return {**DEFAULT_SOURCES, **store.load("sources", {})}


def save_sources(sites, queries):
    clean_sites = []
    for s in sites:
        d = _domain(s.strip())
        if d and d not in clean_sites:
            clean_sites.append(d)
    data = {"sites": clean_sites[:30], "queries": [q.strip() for q in queries if q.strip()][:20]}
    store.save("sources", data)
    return data


def _fill(template, location, industry, role):
    q = template.format(loc=location or "", industry=industry or "", role=role or "")
    return re.sub(r"\s+", " ", q).strip()


def _queries(category, location, industry, role):
    cat = CATEGORIES[category]
    if cat["source"] == "custom":
        src = load_sources()
        words = " ".join(x for x in [role or industry, location] if x) or "internship"
        out = [f"site:{s} {words}" for s in src["sites"]]
        out += [_fill(q, location, industry, role) for q in src["queries"]]
        return out
    return [_fill(q, location, industry, role) for q in cat["queries"]]


def _domain(url):
    try:
        host = urlparse(url if "://" in url else "https://" + url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _existing_domains():
    return {_domain(j.get("website") or j.get("url") or "") for j in store.load("jobs", [])} - {""}


def _default_role(role):
    if role:
        return role
    targets = (store.load("profile", {}).get("targets") or {})
    return (targets.get("roles") or ["software engineering"])[0][:40]


def search(category, location="", industry="", role="", max_queries=4):
    if category not in CATEGORIES:
        raise llm.LLMError("Unknown category.")
    source = CATEGORIES[category]["source"]
    if source == "simplify":
        return _simplify(category, location, role)
    if source == "hn":
        return _hacker_news(location, role)
    if not services.engine_name():
        raise llm.LLMError("Web search needs a Tavily or Serper key. Add one in Settings, then press Save settings.")
    role = _default_role(role)
    queries = _queries(category, location, industry, role)
    if not queries:
        raise llm.LLMError("Add some sites or search phrases under My sources first.")
    limit = 8 if source == "custom" else max_queries
    results, seen = [], set()
    for q in queries[:limit]:
        found = services.find_search({"query": q, "max_results": 10})
        for r in found.get("results", []):
            if r["url"] not in seen:
                seen.add(r["url"])
                results.append({**r, "query": q})
    return {"candidates": _screen(results, CATEGORIES[category]), "queries": queries[:limit], "engine": services.engine_name()}


def _screen(results, cat):
    """Have the AI turn raw search results into firm/posting candidates, scored with the guide's rules."""
    if not results:
        return []
    targets = json.dumps((store.load("profile", {}).get("targets") or {}), ensure_ascii=False)
    rules = prompts.OUTREACH_RULES if cat["kind"] == "firm" else prompts.POSTING_RULES
    out = llm.chat_json([
        {"role": "system", "content": prompts.SCREEN_SYSTEM.format(category=cat["label"], kind=cat["kind"], rules=rules, targets=targets)},
        {"role": "user", "content": json.dumps(results, ensure_ascii=False)},
    ], max_tokens=16000, temperature=0.2, timeout=180)
    have = _existing_domains()
    candidates = []
    for c in out.get("candidates") or []:
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        website = str(c.get("website") or "").strip()
        dom = _domain(website)
        too_big = bool(BIG_FIRMS.search(name))
        candidates.append({
            "name": name, "website": website, "domain": dom, "kind": c.get("kind") or cat["kind"],
            "title": c.get("title") or "", "posting_url": c.get("posting_url") or "",
            "location": c.get("location") or "", "size_hint": c.get("size_hint") or "",
            "fit_score": 10 if too_big else int(c.get("fit_score") or 0),
            "reasons": c.get("reasons") or "", "red_flags": (c.get("red_flags") or "") + (" Large, well-known firm." if too_big else ""),
            "source_url": c.get("source_url") or "", "category": cat["label"],
            "already_added": bool(dom and dom in have),
        })
    candidates.sort(key=lambda c: -c["fit_score"])
    return candidates


# ---------- SimplifyJobs internship lists ----------

_simplify_cache = {}


def _simplify_rows(filename):
    """Parse the list's HTML tables into rows. Cached for an hour (the files are 1-2 MB)."""
    cached = _simplify_cache.get(filename)
    if cached and time.time() - cached[0] < 3600:
        return cached[1]
    r = None
    for attempt in range(2):  # GitHub sometimes stalls on these 1-2 MB files; one retry usually works
        try:
            r = httpx.get(SIMPLIFY_REPO + filename, timeout=httpx.Timeout(20, read=60))
            r.raise_for_status()
            break
        except httpx.HTTPError as e:
            if attempt:
                if cached:  # an older copy is better than nothing
                    return cached[1]
                raise llm.LLMError(f"Couldn't load the SimplifyJobs list ({e.__class__.__name__}). Try again later.")
    rows, section = [], ""
    for part in re.split(r"\n(?=## )", r.text):
        heading = part.split("\n", 1)[0]
        if heading.startswith("## "):
            section = re.sub(r"[^\w&/ ,()-]", "", heading[3:]).strip()
        if "<table" not in part:
            continue
        company = ""
        for tr in BeautifulSoup(part, "html.parser").find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue
            name = tds[0].get_text(" ", strip=True)
            company = company if name.startswith("↳") else re.sub(r"^[^\w(]+", "", name).strip()
            apply = next((a["href"] for a in tr.find_all("a", href=True)
                          if (a.find("img") and a.find("img").get("alt") == "Apply")), "")
            off_season = len(tds) >= 6
            rows.append({
                "company": company, "role": tds[1].get_text(" ", strip=True),
                "location": "; ".join(t.strip() for t in tds[2].get_text("\n", strip=True).split("\n") if t.strip() and not t.strip().endswith("locations")),
                "term": tds[3].get_text(" ", strip=True) if off_season else "Summer 2027",
                "apply": apply, "age": tds[-1].get_text(" ", strip=True), "section": section,
            })
    _simplify_cache[filename] = (time.time(), rows)
    return rows


def _age_days(age):
    m = re.match(r"(\d+)\s*(d|mo|w|y)", age or "")
    if not m:
        return 9999
    n, unit = int(m.group(1)), m.group(2)
    return n * {"d": 1, "w": 7, "mo": 30, "y": 365}[unit]


def _simplify(category, location, role):
    rows = [r for r in _simplify_rows(CATEGORIES[category]["file"]) if r["apply"]]  # no link = closed
    words = [w for w in re.split(r"\W+", (role or "").lower()) if len(w) > 2 and w not in {"intern", "internship", "and", "the"}]
    if words:
        rows = [r for r in rows if any(w in f"{r['role']} {r['section']} {r['company']}".lower() for w in words)]
    if location:
        loc = location.lower()
        rows = [r for r in rows if loc in r["location"].lower() or (loc == "remote" and "remote" in r["location"].lower())]
    rows.sort(key=lambda r: _age_days(r["age"]))
    have = _existing_domains()
    return {"engine": "SimplifyJobs list", "queries": [f"{CATEGORIES[category]['label']}: {role or 'all roles'}{', ' + location if location else ''}"],
            "candidates": [{
                "name": r["company"], "title": r["role"], "kind": "posting", "posting_url": r["apply"], "website": "",
                "domain": _domain(r["apply"]), "location": r["location"], "size_hint": r["term"],
                "fit_score": 60, "reasons": f"{r['section'] or 'Internship'} · posted {r['age']} ago", "red_flags": "",
                "source_url": "https://github.com/SimplifyJobs/Summer2027-Internships", "category": CATEGORIES[category]["label"],
                "already_added": False,
            } for r in rows[:80]]}


# ---------- Hacker News "Who is hiring?" ----------

def _hacker_news(location, role):
    try:
        stories = httpx.get("https://hn.algolia.com/api/v1/search_by_date",
                            params={"tags": "story,author_whoishiring", "hitsPerPage": 6}, timeout=20).json()["hits"]
        story = next(s for s in stories if "who is hiring" in s["title"].lower())
        query = " ".join(x for x in [role or "intern", location] if x)
        hits = httpx.get("https://hn.algolia.com/api/v1/search",
                         params={"tags": f"comment,story_{story['objectID']}", "query": query, "hitsPerPage": 40}, timeout=20).json()["hits"]
    except (httpx.HTTPError, StopIteration, KeyError, ValueError) as e:
        raise llm.LLMError(f"Couldn't reach Hacker News ({e.__class__.__name__}). Try again later.")
    results = []
    for h in hits:
        text = html.unescape(re.sub(r"<[^>]+>", " ", h.get("comment_text") or "")).strip()
        link = re.search(r"https?://[^\s<>\"]+", html.unescape(h.get("comment_text") or ""))
        results.append({"title": text.split("\n")[0][:160], "url": link.group(0) if link else f"https://news.ycombinator.com/item?id={h['objectID']}",
                        "content": text[:700], "query": story["title"]})
    if not results:
        return {"candidates": [], "queries": [f"{story['title']}: {query}"], "engine": "Hacker News"}
    return {"candidates": _screen(results, CATEGORIES["hn_hiring"]), "queries": [f"{story['title']}: {query}"], "engine": "Hacker News"}


# ---------- Imported lists ----------

def parse_import(text):
    """Turn a pasted list or CSV (Axial, Apollo, SearchFunder exports) into candidates without AI."""
    text = text.strip()
    rows = []
    first = text.splitlines()[0] if text else ""
    if "," in first and any(k in first.lower() for k in ("company", "name", "website", "domain", "firm")):
        for row in csv.DictReader(io.StringIO(text)):
            low = {k.lower().strip(): (v or "").strip() for k, v in row.items() if k}
            name = low.get("company") or low.get("company name") or low.get("firm") or low.get("name") or low.get("organization")
            site = low.get("website") or low.get("company website") or low.get("domain") or low.get("url") or ""
            person = " ".join(x for x in [low.get("first name"), low.get("last name")] if x) or low.get("contact") or ""
            rows.append({"name": name or _domain(site), "website": site, "contact_name": person,
                         "contact_title": low.get("title") or "", "contact_email": low.get("email") or ""})
    else:
        for line in text.splitlines():
            line = line.strip(" -•\t")
            if not line:
                continue
            url = re.search(r"https?://\S+|\b[\w-]+\.(com|net|org|io|co|vc|capital|partners|us)\b\S*", line)
            site = url.group(0) if url else ""
            name = (line.replace(site, "").strip(" ,|-–") or _domain(site))
            rows.append({"name": name, "website": site})
    have = _existing_domains()
    return [{**r, "domain": _domain(r["website"]), "kind": "firm", "fit_score": 0, "reasons": "Imported",
             "category": "imported", "already_added": bool(_domain(r["website"]) in have)} for r in rows if r["name"]]
