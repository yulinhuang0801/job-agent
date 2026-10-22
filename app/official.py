"""Official employer pages for company prep, fetched politely.

- A registry (app/official_sources.json) lists employers' own interview, hiring-process and values
  pages that were checked by hand. Companies not in it are looked up on their own domain only.
- Every fetch obeys robots.txt (RFC 9309: user-agent groups, Allow/Disallow with * and $, longest
  match wins, Allow wins a tie; an unreachable robots.txt means "don't fetch"). A short pause
  between requests to the same host. Pages are cached in data/official/ for 30 days.
- Only the user's own copy is cached; the app shows links and uses the text as prep material, it
  doesn't republish pages."""
import hashlib
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from . import services, store

USER_AGENT = "JobAgent/1.0 (interview prep for students; +https://github.com/yulinhuang0801/job-agent)"
ROBOTS_NAME = "jobagent"  # the product token matched against robots.txt groups
CACHE = store.DATA_DIR / "official"
REGISTRY = json.loads((Path(__file__).resolve().parent / "official_sources.json").read_text(encoding="utf-8"))
PAGE_TTL = 30 * 86400
ROBOTS_TTL = 7 * 86400
HOST_GAP = 2.0

_last_hit = {}
_lock = threading.Lock()


# ---------- robots.txt ----------

def _robots_groups(text):
    """[(agents, [(allow, path)])] from a robots.txt body."""
    groups, agents, rules, last = [], [], [], None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (x.strip() for x in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if last == "rule":  # a new group starts
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value.lower())
            last = "agent"
        elif key in ("allow", "disallow"):
            rules.append((key == "allow", value))
            last = "rule"
    if agents:
        groups.append((agents, rules))
    return groups


def _pattern(path):
    """robots path pattern → regex (* = anything, trailing $ = end)."""
    end = path.endswith("$")
    body = re.escape(path[:-1] if end else path).replace(r"\*", ".*")
    return re.compile("^" + body + ("$" if end else ""))


def robots_allows(robots_text, url, agent=ROBOTS_NAME):
    groups = _robots_groups(robots_text)
    mine = [r for a, r in groups if any(x != "*" and x in agent for x in a)]
    rules = [rule for g in mine for rule in g] if mine else [rule for a, r in groups if "*" in a for rule in r]
    p = urlparse(url)
    target = (p.path or "/") + (("?" + p.query) if p.query else "")
    best = None  # (length, allow)
    for allow, path in rules:
        if not path:
            continue  # "Disallow:" with nothing allows everything
        if _pattern(path).match(target):
            key = (len(path), allow)
            if best is None or key > best:
                best = key
    return True if best is None else best[1]


def _robots_text(host):
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"robots_{host}.json"
    if f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))
        if time.time() - d["at"] < ROBOTS_TTL:
            return d["text"], d["ok"]
    try:
        r = httpx.get(f"https://{host}/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=15, follow_redirects=True)
        if r.status_code >= 500:
            text, ok = "", False  # unreachable: assume we may not crawl
        elif r.status_code >= 400:
            text, ok = "", True   # no robots.txt: crawling allowed
        else:
            text, ok = r.text, True
    except httpx.HTTPError:
        text, ok = "", False
    f.write_text(json.dumps({"text": text, "ok": ok, "at": time.time()}), encoding="utf-8")
    return text, ok


def allowed(url):
    host = urlparse(url).netloc
    text, ok = _robots_text(host)
    return ok and robots_allows(text, url)


# ---------- Fetching ----------

def _pause(host):
    with _lock:
        wait = _last_hit.get(host, 0) + HOST_GAP - time.time()
        _last_hit[host] = max(time.time(), _last_hit.get(host, 0) + HOST_GAP)
    if wait > 0:
        time.sleep(wait)


def _clean_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "form", "svg", "iframe"]):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    lines = [re.sub(r"\s+", " ", t).strip() for t in root.get_text("\n").splitlines()]
    text = "\n".join(l for l in lines if len(l) > 2)
    title = (soup.title.get_text(strip=True) if soup.title else "")[:120]
    return title, text


def fetch(url):
    """A page's title and text, or a reason it wasn't fetched. Cached for 30 days."""
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / ("page_" + hashlib.sha1(url.encode()).hexdigest()[:16] + ".json")
    if f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))
        if time.time() - d.get("at", 0) < PAGE_TTL:
            return d
    if not allowed(url):
        return {"url": url, "ok": False, "reason": "robots.txt asks crawlers not to fetch this page", "at": time.time()}
    host = urlparse(url).netloc
    _pause(host)
    try:
        r = httpx.get(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}, timeout=25, follow_redirects=True)
    except httpx.HTTPError as e:
        return {"url": url, "ok": False, "reason": f"couldn't load it ({e.__class__.__name__})", "at": time.time()}
    final = str(r.url)
    if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
        return {"url": url, "ok": False, "reason": f"the site answered {r.status_code}", "at": time.time()}
    if final != url and not allowed(final):
        return {"url": url, "ok": False, "reason": "redirected to a page robots.txt blocks", "at": time.time()}
    title, text = _clean_text(r.text)
    d = {"url": url, "final_url": final, "ok": True, "title": title, "text": text[:30000], "at": time.time(),
         "fetched": datetime.now().isoformat(timespec="seconds")}
    f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return d


def get_html(url):
    """Raw HTML of a page, robots.txt-checked and paused like fetch(); None if blocked or unreachable. Not cached."""
    if not allowed(url):
        return None
    _pause(urlparse(url).netloc)
    try:
        r = httpx.get(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}, timeout=20, follow_redirects=True)
    except httpx.HTTPError:
        return None
    if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
        return None
    final = str(r.url)
    if final != url and not allowed(final):
        return None
    return {"url": final, "html": r.text[:600000]}


# ---------- Which pages belong to a company ----------

def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def employer(company):
    key = _norm(company)
    if not key:
        return None
    for e in REGISTRY["employers"]:
        names = [e["name"]] + e.get("aliases", [])
        if any(_norm(n) == key or (len(_norm(n)) > 3 and (_norm(n) in key or key in _norm(n))) for n in names):
            return e
    return None


def _official_domain(company, website=""):
    """The company's own domain: from its website if known, else a search result whose domain carries its name."""
    if website:
        host = urlparse(website if "://" in website else "https://" + website).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    if not services.search_enabled():
        return ""
    found = services.web_search({"query": f"{company} careers official site", "max_results": 6})
    key = _norm(company)
    for r in found.get("results", []):
        host = urlparse(r.get("url", "")).netloc.lower()
        parts = host.split(".")
        label = _norm(parts[-2] if len(parts) >= 2 else host)  # "gene" in careers.gene.com
        # The domain carries the company's name, or a shortened form of it (gene.com for Genentech).
        if key and len(label) >= 3 and (key.startswith(label) or label.startswith(key[:12])):
            return ".".join(parts[-2:])
    return ""


DISCOVER = re.compile(r"interview|hiring|how we hire|recruit|internship|students|university|early.career|graduate|faq|what we look for|values|culture", re.I)
RELEVANT = re.compile(r"interview|assessment|hiring process|how we hire|case|prepare|preparation|what we look for|values|principles|culture|tips", re.I)


def _discover(company, domain):
    """Interview-related pages on the company's own domain, via search limited to that domain."""
    if not (domain and services.search_enabled()):
        return []
    found = services.web_search({"query": f"{company} interview process how we hire tips", "max_results": 6, "include_domains": [domain]})
    urls, seen = [], set()
    for r in found.get("results", []):
        u = r.get("url", "")
        p = urlparse(u)
        host, key = p.netloc.lower(), p.netloc.lower().removeprefix("www.") + p.path.rstrip("/")
        if key in seen:
            continue  # the same page over http and https
        if (host == domain or host.endswith("." + domain)) and DISCOVER.search(r.get("title", "") + " " + u):
            seen.add(key)
            urls.append({"url": u.replace("http://", "https://", 1), "about": r.get("title", "")[:80]})
    return urls[:3]


def _excerpt(text, n=3500):
    """Keep the paragraphs about interviewing, values and preparation first."""
    paras = [p for p in text.split("\n") if len(p) > 40]
    hits = [p for p in paras if RELEVANT.search(p)]
    rest = [p for p in paras if p not in hits]
    out, size = [], 0
    for p in hits + rest:
        if size + len(p) > n:
            break
        out.append(p)
        size += len(p)
    return "\n".join(out)


def for_company(company, website=""):
    """Official pages for a company: [{url, title, about, excerpt}] plus pages skipped and why."""
    e = employer(company)
    pages = list(e["pages"]) if e else []
    source = "registry" if e else "discovered"
    if not pages:
        pages = _discover(company, _official_domain(company, website))
    used, skipped = [], []
    for p in pages[:4]:
        d = fetch(p["url"])
        if d.get("ok") and len(d.get("text", "")) > 200:
            used.append({"url": d.get("final_url") or p["url"], "title": d.get("title") or p.get("about", ""), "about": p.get("about", ""),
                         "excerpt": _excerpt(d["text"]), "source": source})
        else:
            skipped.append({"url": p["url"], "reason": d.get("reason", "no readable text")})
    return {"employer": e["name"] if e else company, "pages": used, "skipped": skipped,
            "links_only": e.get("links_only", []) if e else []}
