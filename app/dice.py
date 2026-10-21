"""Dice job search through Dice's official MCP server (https://mcp.dice.com/mcp, no login).

A minimal MCP client: JSON-RPC 2.0 over HTTP POST (Streamable HTTP transport). Replies come as JSON or as
text/event-stream, whose `data:` lines hold the JSON-RPC message. If the server hands out an
mcp-session-id it is sent back on every later request.

Dice tags almost every internship as "Full-time" (the INTERNSHIP employment-type filter returns nothing),
so searches use "<role> intern" as the keyword and the job type comes from the title, like other sources.
"""
import json
import re
import time
from datetime import datetime

import httpx

from . import crawlers, db, store, taxonomy

URL = "https://mcp.dice.com/mcp"
NAME = "Dice"
PROTOCOL = "2025-03-26"
MAX_SEARCHES = 4  # per run; Dice's server is shared, keep each run to a handful of requests
PER_PAGE = 50
PAUSE = 1.0  # seconds between requests
COOLDOWN = 120  # seconds between runs
# Words in a target role that describe the job rather than the field ("Remote unpaid tech internship (...)").
_FILLER = r"\b(remote|hybrid|on-?site|unpaid|paid|part-?time|full-?time|summer|fall|winter|spring|20\d\d|internships?|interns?|" \
          r"co-?ops?|roles?|jobs?|positions?|exploring|interested in|any|entry[- ]level|new grad|freshman|sophomore|junior|senior)\b"


class DiceError(crawlers.CrawlError):
    pass


class Client:
    """Talks to one MCP server over Streamable HTTP. Use as a context manager."""

    def __init__(self, url=URL, transport=None, pause=PAUSE):
        self.url, self.pause, self.session, self._id, self._last = url, pause, None, 0, 0.0
        self.http = httpx.Client(timeout=httpx.Timeout(20, read=60), transport=transport,
                                 headers={"Accept": "application/json, text/event-stream", "User-Agent": "JobAgent/1.0"})

    def __enter__(self):
        self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                    "clientInfo": {"name": "job-agent", "version": "1.0"}})
        self.notify("notifications/initialized")
        return self

    def __exit__(self, *exc):
        self.http.close()

    def _post(self, body):
        wait = self.pause - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        headers = {"mcp-session-id": self.session} if self.session else {}
        for attempt in range(3):
            try:
                r = self.http.post(self.url, json=body, headers=headers)
            except httpx.HTTPError as e:
                raise DiceError(f"Couldn't reach Dice ({e.__class__.__name__}). Check your internet connection and try again.")
            self._last = time.monotonic()
            if r.status_code != 429:
                break
            if attempt == 2:
                raise DiceError("Dice is limiting requests right now. Try again in a few minutes.")
            try:
                delay = min(30.0, float(r.headers.get("retry-after", "")))
            except ValueError:
                delay = 5.0 * (attempt + 1)
            time.sleep(delay)
        if r.status_code >= 400:
            raise DiceError(f"Dice's job search answered with an error (HTTP {r.status_code}). Try again later.")
        self.session = r.headers.get("mcp-session-id") or self.session
        return r

    @staticmethod
    def _parse(r):
        if "text/event-stream" in r.headers.get("content-type", ""):
            messages, data = [], []
            for line in r.text.splitlines() + [""]:
                if line.startswith("data:"):
                    data.append(line[5:].lstrip())
                elif not line.strip() and data:  # a blank line ends one event
                    messages.append("\n".join(data))
                    data = []
            texts = messages
        else:
            texts = [r.text] if r.text.strip() else []
        out = []
        for t in texts:
            try:
                out.append(json.loads(t))
            except ValueError:
                continue
        return out

    def request(self, method, params=None):
        self._id += 1
        body = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            body["params"] = params
        replies = [m for m in self._parse(self._post(body)) if isinstance(m, dict) and m.get("id") == self._id]
        if not replies:
            raise DiceError("Dice's job search sent back an empty reply. Try again later.")
        if "error" in replies[0]:
            raise DiceError(f"Dice's job search said: {(replies[0]['error'] or {}).get('message', 'unknown error')}")
        return replies[0].get("result") or {}

    def notify(self, method):
        self._post({"jsonrpc": "2.0", "method": method})

    def tools(self):
        return {t["name"]: t for t in self.request("tools/list").get("tools", [])}

    def call(self, name, arguments):
        res = self.request("tools/call", {"name": name, "arguments": arguments})
        if res.get("isError"):
            text = " ".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
            raise DiceError(f"Dice couldn't run that search: {text[:200] or 'unknown error'}")
        if isinstance(res.get("structuredContent"), dict):
            return res["structuredContent"]
        for c in res.get("content", []):
            if c.get("type") == "text":
                try:
                    return json.loads(c["text"])
                except ValueError:
                    pass
        return {}


# ---------- what to search ----------

def keywords_from_targets(targets, limit=MAX_SEARCHES):
    """Short search keywords from the profile's target roles.
    "Remote unpaid tech internship (exploring software engineering, data/ML, QA)" → ["software engineering", "data ML", "QA"]."""
    out = []
    for role in (targets or {}).get("roles") or []:
        role = str(role)
        inner = re.findall(r"\(([^)]*)\)", role)
        parts = re.split(r"[,;]| or | and ", " , ".join(inner)) if inner else re.split(r"[,;]| or ", role)
        for p in parts:
            p = re.sub(_FILLER, " ", p.replace("/", " "), flags=re.I)
            p = re.sub(r"\s+", " ", p).strip(" -")
            if len(p) >= 2 and p.lower() not in (k.lower() for k in out):
                out.append(p)
    return out[:limit]


def wants_internships(targets):
    text = " ".join(str(v) for v in [(targets or {}).get("seniority", "")] + list((targets or {}).get("roles") or []))
    return not text.strip() or bool(re.search(r"intern|co-?op|student|freshman|sophomore|junior", text, re.I))


def plan(settings=None, profile=None):
    """The searches the next run would make: {keywords, sponsor, remote, location, interns}."""
    s = settings if settings is not None else store.get_settings()
    targets = (profile if profile is not None else store.load("profile", {})).get("targets") or {}
    saved = [k.strip() for k in str(s.get("dice_keywords") or "").split(",") if k.strip()]
    locations = [str(x) for x in targets.get("locations") or []]
    remote = any(re.search(r"remote|anywhere", x, re.I) for x in locations)
    return {"keywords": (saved or keywords_from_targets(targets) or ["software engineer"])[:MAX_SEARCHES],
            "sponsor": s.get("dice_sponsor") == "on", "remote": remote,
            "location": "" if remote or not locations else locations[0], "interns": wants_internships(targets)}


# ---------- Dice result → postings row ----------

def posting(job, sponsor_filter=False):
    """One search_jobs result as a postings row (same classification as the other sources)."""
    loc = ((job.get("jobLocation") or {}).get("displayName") or "").strip()
    remote = bool(job.get("isRemote")) or "Remote" in (job.get("workplaceTypes") or [])
    if remote and "remote" not in loc.lower():
        loc = f"Remote; {loc}" if loc else "Remote"
    summary = re.sub(r"\s+", " ", job.get("summary") or "").strip()
    # Dice's willingToSponsor is a yes-flag; false only means the employer didn't tick it, not "no sponsorship".
    sponsorship = "Willing to sponsor" if job.get("willingToSponsor") or sponsor_filter else taxonomy.sponsorship_hint(summary)
    return {"id": f"dice:{job.get('guid') or job.get('id')}", "title": (job.get("title") or "").strip(), "department": "",
            "location": loc, "url": job.get("detailsPageUrl") or f"https://www.dice.com/job-detail/{job.get('guid')}",
            "posted": (job.get("postedDate") or "")[:10], "description": summary[:3000], "sponsorship": sponsorship,
            # Dice labels internships "Full-time"; trust the title for interns and its label otherwise.
            "commitment": "" if re.search(r"intern|co-?op", job.get("title") or "", re.I) else (job.get("employmentType") or ""),
            "company": (job.get("companyName") or "").strip(), "remote": remote}


def search(client, keyword, sponsor=False, remote=False, location="", interns=True):
    args = {"keyword": f"{keyword} intern" if interns and not re.search(r"\bintern", keyword, re.I) else keyword,
            "jobs_per_page": PER_PAGE, "page_number": 1}
    if sponsor:
        args["willing_to_sponsor"] = True
    if remote:
        args["workplace_types"] = ["Remote"]
    elif location:
        args["location"] = location
    return [j for j in (client.call("search_jobs", args).get("data") or []) if j.get("guid") or j.get("id")]


def run(transport=None, pause=PAUSE, progress=None):
    """Search Dice for the user's target roles and replace the Dice rows in the postings table. Returns (rows, message)."""
    p = plan()
    say = progress or (lambda m: crawlers._progress(NAME, m))
    found, failed, error = {}, [], None
    with Client(transport=transport, pause=pause) as client:
        schema = (client.tools().get("search_jobs") or {}).get("inputSchema", {}).get("properties", {})
        if "keyword" not in schema:
            raise DiceError("Dice's job search tool has changed; Job Agent needs an update to use it.")
        if p["sponsor"] and "willing_to_sponsor" not in schema:
            raise DiceError("Dice no longer offers the 'willing to sponsor' filter. Turn it off and try again.")
        for kw in p["keywords"]:
            say(f"Searching Dice for {kw}{' internships' if p['interns'] else ''}…")
            try:
                jobs = search(client, kw, p["sponsor"], p["remote"], p["location"], p["interns"])
            except DiceError as e:
                if "limiting" in str(e):
                    raise
                failed.append(kw)
                error = e
                continue
            for j in jobs:
                row = posting(j, p["sponsor"])
                found.setdefault(row["id"], row)
    if error and len(failed) == len(p["keywords"]):
        raise error
    rows = [r for r in found.values() if r["title"] and r["company"]]
    if p["interns"]:
        rows = [r for r in rows if taxonomy.classify_job_type(r["title"], r["commitment"]) in ("Internship", "Co-op")]
    with db.connect() as con:
        known = {r["name"]: r["industry"] for r in con.execute("SELECT name, industry FROM companies WHERE industry != ''")}
        known.update({r["company"]: r["industry"] for r in con.execute("SELECT DISTINCT company, industry FROM postings WHERE industry != ''")})
    missing = sorted({r["company"] for r in rows} - set(known))
    if missing:
        say(f"Sorting {len(missing)} companies into industries…")
        known.update(crawlers._classify_industries(missing))
    out = [crawlers._posting_row(r, r["company"], "dice", NAME, known.get(r["company"], "")) for r in rows]
    with db.connect() as con:
        con.execute("DELETE FROM postings WHERE source = ?", (NAME,))
        db.upsert(con, "postings", out)
    what = f"{'internships' if p['interns'] else 'jobs'} for {', '.join(k for k in p['keywords'] if k not in failed)}"
    msg = f"{len(out)} {what}" + (" from employers willing to sponsor" if p["sponsor"] else "") + (" (remote)" if p["remote"] else "")
    if failed:
        msg += f"; couldn't search {', '.join(failed)}"
    if not out and p["sponsor"]:
        msg += ". Few Dice postings carry the sponsorship flag; try with it off and use \"Hide no sponsorship\" below instead"
    return len(out), msg


def last_run_age():
    s = db.sources().get(NAME) or {}
    try:
        return (datetime.now() - datetime.fromisoformat(s["updated"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        return None
