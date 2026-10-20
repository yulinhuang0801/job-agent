"""Outside question sources for the Skills check and Company prep (all MIT-licensed, fetched at
runtime and cached in data/qlib/, never copied into this repo):

- Tech Interview Handbook (yangshun/tech-interview-handbook): per-topic LeetCode lists with
  corner cases and techniques, and behavioral questions reported at specific companies.
- LeetCode problems through alfa-leetcode-api (statement, examples, starter code, hints), with
  LeetCode's public GraphQL as a fallback when that service is asleep.
- Open Interview (open-interview/open-interview): thousands of concept questions with reference
  answers, by channel (system design, backend, ML, ...)."""
import json
import random
import re
import threading
import time
from html import unescape

import httpx
from bs4 import BeautifulSoup

from . import store

CACHE = store.DATA_DIR / "qlib"
HANDBOOK_RAW = "https://raw.githubusercontent.com/yangshun/tech-interview-handbook/main/apps/website/contents/"
HANDBOOK_SITE = "https://www.techinterviewhandbook.org/"
ALFA = "https://alfa-leetcode-api.onrender.com"
OI_RAW = "https://raw.githubusercontent.com/open-interview/open-interview/main/data/questions/"
OI_SITE = "https://open-interview.github.io/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Job Agent; interview practice)"}

HANDBOOK_TOPICS = {
    "array": "Array", "string": "String", "hash-table": "Hash table", "recursion": "Recursion",
    "sorting-searching": "Sorting and searching", "matrix": "Matrix", "linked-list": "Linked list", "queue": "Queue",
    "stack": "Stack", "interval": "Interval", "tree": "Tree", "graph": "Graph", "heap": "Heap", "trie": "Trie",
    "dynamic-programming": "Dynamic programming", "binary": "Binary", "math": "Math", "geometry": "Geometry",
}

# Open Interview channels worth offering to students (the repo also has many cloud-cert channels).
OI_CHANNELS = {
    "system-design": "System design", "algorithms": "Algorithms", "data-structures": "Data structures",
    "backend": "Backend", "frontend": "Frontend", "database": "Databases", "python": "Python",
    "operating-systems": "Operating systems", "networking": "Networking", "concurrency": "Concurrency",
    "machine-learning": "Machine learning", "generative-ai": "Generative AI", "nlp": "NLP", "computer-vision": "Computer vision",
    "data-engineering": "Data engineering", "devops": "DevOps", "kubernetes": "Kubernetes", "aws": "AWS", "security": "Security",
    "testing": "Testing", "design-patterns": "Design patterns", "low-level": "Low-level design", "ios": "iOS", "android": "Android",
    "sre": "SRE", "linux": "Linux", "behavioral": "Behavioral",
}

LEVELS = {"Easy": 1, "Medium": 2, "Hard": 3, "beginner": 1, "intermediate": 2, "advanced": 3}


class LibraryError(Exception):
    pass


def _path(name):
    CACHE.mkdir(parents=True, exist_ok=True)
    return CACHE / name


def _cached(name, max_age_days):
    p = _path(name)
    if p.exists() and time.time() - p.stat().st_mtime < max_age_days * 86400:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return None
    return None


def _stale(name):
    p = _path(name)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except ValueError:
        return None


def _write(name, data):
    _path(name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def _get(url, timeout=40):
    r = httpx.get(url, headers=HEADERS, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    return r


# ---------- Tech Interview Handbook ----------

def _md_sections(md):
    """{'## heading': text} for a markdown page."""
    out, head, buf = {}, "", []
    for line in md.splitlines():
        if line.startswith("## "):
            out[head] = "\n".join(buf).strip()
            head, buf = line[3:].strip(), []
        else:
            buf.append(line)
    out[head] = "\n".join(buf).strip()
    return out


def _plain(md, n=900):
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", md)
    text = re.sub(r"<[^>]+>|^import .*$|^\s*\|.*\|\s*$", "", text, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:n]


def _slugs(md):
    return list(dict.fromkeys(re.findall(r"leetcode\.com/problems/([a-z0-9-]+)", md)))


def handbook():
    """Topic lists and company behavioral questions, refreshed weekly."""
    data = _cached("handbook.json", 7)
    if data:
        return data
    try:
        topics = {}
        for slug, label in HANDBOOK_TOPICS.items():
            s = _md_sections(_get(HANDBOOK_RAW + f"algorithms/{slug}.md").text)
            essential = _slugs(s.get("Essential questions", ""))
            recommended = [x for x in _slugs(s.get("Recommended practice questions", "")) if x not in essential]
            topics[slug] = {"label": label, "essential": essential, "recommended": recommended,
                            "look_out": _plain(s.get("Things to look out for during interviews", ""), 600),
                            "corner_cases": _plain(s.get("Corner cases", ""), 500),
                            "techniques": _plain(s.get("Techniques", ""), 1200),
                            "url": HANDBOOK_SITE + f"algorithms/{slug}"}
        companies = {}
        for head, body in _md_sections(_get(HANDBOOK_RAW + "behavioral-interview-questions.md").text).items():
            m = re.match(r"(.+?) Software Engineer behavioral interview questions", head)
            if m:
                qs = [re.sub(r"^\s*(\d+\.|[-*])\s*", "", l).strip() for l in body.splitlines() if re.match(r"\s*(\d+\.|[-*])\s+\S", l)]
                companies[m.group(1).strip()] = [_plain(q, 300) for q in qs if len(q) > 12]
        return _write("handbook.json", {"topics": topics, "companies": companies, "url": HANDBOOK_SITE})
    except (httpx.HTTPError, OSError) as e:
        old = _stale("handbook.json")
        if old:
            return old
        raise LibraryError(f"Couldn't download the Tech Interview Handbook ({e.__class__.__name__}).")


def company_behavioral(company):
    """Behavioral questions the handbook lists for this company, if any."""
    try:
        companies = handbook()["companies"]
    except LibraryError:
        return []
    key = company.strip().lower()
    for name, qs in companies.items():
        if name.lower() == key or (len(key) > 3 and (key in name.lower() or name.lower() in key)):
            return qs
    return []


# ---------- LeetCode ----------

_GRAPHQL = """query q($titleSlug: String!) { question(titleSlug: $titleSlug) { questionFrontendId title titleSlug content
  difficulty isPaidOnly exampleTestcases hints metaData topicTags { name slug } codeSnippets { langSlug code } } }"""

LANG_SLUGS = {"python": "python3", "java": "java", "cpp": "cpp", "c": "c", "javascript": "javascript", "typescript": "typescript", "go": "golang"}


def _html_text(html):
    soup = BeautifulSoup(html or "", "html.parser")
    for sup in soup.find_all("sup"):
        sup.replace_with("^" + sup.get_text())
    for pre in soup.find_all("pre"):
        pre.replace_with("\n" + pre.get_text().strip("\n") + "\n")
    for li in soup.find_all("li"):
        li.insert(0, "• ")
    text = soup.get_text()
    text = unescape(text).replace("\xa0", " ")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def problem(slug):
    """One LeetCode problem, cached for 90 days. Raises LibraryError for paid-only or missing problems."""
    slug = re.sub(r"[^a-z0-9-]", "", slug.lower())
    name = f"lc/{slug}.json"
    (CACHE / "lc").mkdir(parents=True, exist_ok=True)
    data = _cached(name, 90)
    if data:
        return data
    q = None
    try:
        r = _get(f"{ALFA}/select/raw?titleSlug={slug}", timeout=60).json()
        q = r.get("question") if isinstance(r.get("question"), dict) else r
    except (httpx.HTTPError, ValueError):
        q = None
    if not (q and q.get("content")):
        try:
            r = httpx.post("https://leetcode.com/graphql", json={"query": _GRAPHQL, "variables": {"titleSlug": slug}},
                           headers={**HEADERS, "Referer": f"https://leetcode.com/problems/{slug}/"}, timeout=30)
            q = (r.json().get("data") or {}).get("question")
        except (httpx.HTTPError, ValueError):
            q = None
    if not q:
        old = _stale(name)
        if old:
            return old
        raise LibraryError(f"Couldn't load LeetCode problem {slug}.")
    if q.get("isPaidOnly") or not q.get("content"):
        raise LibraryError(f"{q.get('title') or slug} is a LeetCode Premium problem.")
    snippets = {c["langSlug"]: c["code"] for c in q.get("codeSnippets") or []}
    hints = q.get("hints") or []
    data = {"slug": slug, "number": q.get("questionFrontendId"), "title": q.get("title") or slug, "difficulty": q.get("difficulty") or "Medium",
            "text": _html_text(q["content"]), "starter": {k: snippets[v] for k, v in LANG_SLUGS.items() if v in snippets},
            "examples": q.get("exampleTestcases") or "", "meta": q.get("metaData") or "",
            "hints": [_html_text(h) for h in hints][:4], "tags": [t["name"] for t in q.get("topicTags") or []],
            "url": f"https://leetcode.com/problems/{slug}/"}
    return _write(name, data)


def slug_from_link(text):
    m = re.search(r"leetcode\.(?:com|cn)/problems/([a-z0-9-]+)", text or "")
    return m.group(1) if m else re.sub(r"[^a-z0-9-]", "", (text or "").strip().lower().replace(" ", "-"))


def _used():
    return store.load("qlib_used", {"leetcode": [], "oi": []})


def _mark_used(kind, ids):
    used = _used()
    used[kind] = list(dict.fromkeys(used.get(kind, []) + ids))[-2000:]
    store.save("qlib_used", used)


def leetcode_question(p, topic=None, lang="python"):
    """A Skills-check question built from a LeetCode problem."""
    t = (handbook()["topics"].get(topic) if topic else None) or {}
    rubric = (f"LeetCode {p['number']}. {p['title']} ({p['difficulty']}). Judge whether the code solves the problem correctly on the "
              f"examples and on edge cases, and whether its time and space complexity are close to the best known approach. "
              f"The candidate may explain the approach and complexity in comments; reward that.")
    if t.get("corner_cases"):
        rubric += "\nCorner cases to check (Tech Interview Handbook): " + t["corner_cases"][:400]
    if p["hints"]:
        rubric += "\nLeetCode hints: " + " | ".join(p["hints"])[:500]
    return {"id": f"lc_{p['slug']}", "area": f"LeetCode · {t.get('label') or ', '.join(p['tags'][:2]) or 'Coding'}",
            "level": LEVELS.get(p["difficulty"], 2), "type": "practical", "lang": lang,
            "question": f"{p['title']} ({p['difficulty']})\n\n{p['text']}\n\nWrite a solution, then add a comment with its time and space complexity.",
            "starter": p["starter"].get(lang) or p["starter"].get("python") or "", "starters": p["starter"],
            "rubric": rubric, "source": "LeetCode", "link": p["url"],
            "guide": t.get("url", ""), "examples": p["examples"]}


def pick_leetcode(topic, n=1, max_level=3):
    """Problems from the handbook's list for a topic: essential ones first, skipping ones used before."""
    topics = handbook()["topics"]
    if topic not in topics:
        raise LibraryError(f"Unknown LeetCode topic {topic}.")
    used = set(_used()["leetcode"])
    t = topics[topic]
    order = [s for s in t["essential"] if s not in used] + [s for s in t["recommended"] if s not in used]
    if not order:  # every problem done once: start over
        order = t["essential"] + t["recommended"]
    out = []
    for slug in order:
        if len(out) >= n:
            break
        try:
            p = problem(slug)
        except LibraryError:
            continue
        if LEVELS.get(p["difficulty"], 2) <= max_level:
            out.append(leetcode_question(p, topic))
    _mark_used("leetcode", [q["id"][3:] for q in out])
    return out


# ---------- Open Interview ----------

def channel(name):
    """All questions of one Open Interview channel, trimmed to the fields we use, cached for 14 days."""
    if name not in OI_CHANNELS:
        raise LibraryError(f"Unknown Open Interview channel {name}.")
    fname = f"oi_{name}.json"
    data = _cached(fname, 14)
    if data:
        return data
    try:
        raw = _get(OI_RAW + f"{name}.json", timeout=90).json()
    except (httpx.HTTPError, ValueError) as e:
        old = _stale(fname)
        if old:
            return old
        raise LibraryError(f"Couldn't download Open Interview {OI_CHANNELS[name]} ({e.__class__.__name__}).")
    rows = raw if isinstance(raw, list) else raw.get("questions") or []
    out = []
    for q in rows:
        text = (q.get("question") or "").strip()
        if len(text) < 25 or not (q.get("answer") or q.get("explanation")):
            continue
        out.append({"id": str(q.get("id") or len(out)), "question": text[:1500], "answer": (q.get("answer") or "")[:800],
                    "explanation": (q.get("explanation") or "")[:1800], "difficulty": q.get("difficulty") or "intermediate",
                    "tags": (q.get("tags") or [])[:6], "companies": q.get("companies") or []})
    return _write(fname, out)


def oi_question(q, ch):
    return {"id": f"oi_{ch}_{q['id']}", "area": f"Open Interview · {OI_CHANNELS[ch]}", "level": LEVELS.get(q["difficulty"], 2),
            "type": "concept", "question": q["question"],
            "rubric": "Reference answer (Open Interview, community-written; judge the candidate on correctness, not on matching it word "
                      f"for word): {q['answer']}\n\nMore detail: {q['explanation'][:1200]}",
            "source": "Open Interview", "link": OI_SITE}


def pick_oi(ch, n=2, max_level=2):
    used = set(_used()["oi"])
    qs = [q for q in channel(ch) if LEVELS.get(q["difficulty"], 2) <= max_level]
    fresh = [q for q in qs if f"oi_{ch}_{q['id']}" not in used] or qs
    picked = random.sample(fresh, min(n, len(fresh)))
    out = [oi_question(q, ch) for q in picked]
    _mark_used("oi", [q["id"] for q in out])
    return out


def company_technical(company, limit=8):
    """Open Interview questions tagged with this company, from channels already downloaded."""
    key = company.strip().lower()
    if len(key) < 3:
        return []
    found = []
    for ch in OI_CHANNELS:
        for q in _stale(f"oi_{ch}.json") or []:
            if any(key == str(c).lower() or key in str(c).lower() for c in q.get("companies") or []):
                found.append({**q, "channel": ch})
    return found[:limit]


# ---------- What the Skills check can offer ----------

def _prefetch_handbook():
    try:
        handbook()
    except LibraryError:
        pass


def areas():
    """Extra Skills-check areas: 'lc:<topic>' and 'oi:<channel>'. Never blocks on a download: the
    handbook is fetched in the background the first time, and channels when first used."""
    topics = (_stale("handbook.json") or {}).get("topics")
    if not topics:
        threading.Thread(target=_prefetch_handbook, daemon=True).start()
    lc = [{"id": f"lc:{k}", "label": f"LeetCode · {v}", "source": "LeetCode",
           "count": len(topics[k]["essential"]) + len(topics[k]["recommended"]) if topics and k in topics else None}
          for k, v in HANDBOOK_TOPICS.items()]
    oi = []
    for k, v in OI_CHANNELS.items():
        cached = _stale(f"oi_{k}.json")
        oi.append({"id": f"oi:{k}", "label": f"Open Interview · {v}", "count": len(cached) if cached else None, "source": "Open Interview"})
    return lc + oi


def pick(area_id, n, max_level=3):
    kind, _, key = area_id.partition(":")
    if kind == "lc":
        return pick_leetcode(key, n, max_level)
    if kind == "oi":
        return pick_oi(key, n, min(max_level, 2))  # its "advanced" questions are senior-level
    raise LibraryError(f"Unknown source area {area_id}.")
