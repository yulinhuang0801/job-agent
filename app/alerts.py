"""Job-alert emails → the postings database.

LinkedIn, Handshake, Indeed and others email job recommendations to the address on the account.
Instead of logging into those sites (their terms forbid automation), we read only those alert
emails from the user's own inbox (IMAP, read-only, with an app password the user types into
Settings), or from an alert the user pastes or uploads (.eml). Other mail is never fetched: the
IMAP search asks the server only for messages from the alert senders below."""
import email
import hashlib
import imaplib
import json
import re
from datetime import date, datetime, timedelta
from email import policy
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

from . import db, llm, store, taxonomy


class AlertError(Exception):
    pass


# (site, sender domains, job-link pattern, canonical link). A canonical link drops tracking parameters
# and lets the same job from two emails count once.
SITES = [
    ("LinkedIn", ("linkedin.com",), r"linkedin\.com/(?:comm/)?jobs/view/(?:[\w-]*?-)?(\d{6,})",
     lambda m: f"https://www.linkedin.com/jobs/view/{m.group(1)}/"),
    ("Handshake", ("joinhandshake.com",), r"joinhandshake\.com/(?:[\w/-]*?/)?(?:jobs|job-search|postings)/(\d{4,})",
     lambda m: f"https://app.joinhandshake.com/jobs/{m.group(1)}"),
    ("Indeed", ("indeed.com",), r"indeed\.[a-z.]+/(?:rc/clk|viewjob|pagead/clk|m/viewjob|applystart|m/basecamp/viewjob)\S*?[?&](?:amp;)?jk=([0-9a-f]{8,})",
     lambda m: f"https://www.indeed.com/viewjob?jk={m.group(1)}"),
    ("Glassdoor", ("glassdoor.com",), r"glassdoor\.[a-z.]+/\S*?(?:jobListingId=|[?&]jl=)(\d{6,})",
     lambda m: f"https://www.glassdoor.com/job-listing/?jl={m.group(1)}"),
    ("ZipRecruiter", ("ziprecruiter.com",), r"ziprecruiter\.com/(?:c|k|jobs|job)/[^\s\"'<>?#]+", None),
    ("Wellfound", ("wellfound.com", "angel.co"), r"wellfound\.com/(?:jobs|company/[\w-]+/jobs)/(\d+)", lambda m: f"https://wellfound.com/jobs/{m.group(1)}"),
    ("Simplify", ("simplify.jobs",), r"simplify\.jobs/p/([\w-]+)", lambda m: f"https://simplify.jobs/p/{m.group(1)}"),
    ("Jobright", ("jobright.ai",), r"jobright\.ai/jobs/info/(\w+)", lambda m: f"https://jobright.ai/jobs/info/{m.group(1)}"),
    ("Built In", ("builtin.com",), r"builtin\.com/job/[^\s\"'<>?#]+", None),
    ("WayUp", ("wayup.com",), r"wayup\.com/i/[^\s\"'<>?#]+", None),
    ("RippleMatch", ("ripplematch.com",), r"ripplematch\.com/[^\s\"'<>?#]*(?:job|opportunit)[^\s\"'<>?#]*", None),
    ("Welcome to the Jungle", ("welcometothejungle.com", "otta.com"), r"welcometothejungle\.com/\S*?/jobs/[^\s\"'<>?#]+", None),
    ("Google", ("google.com",), r"google\.com/search\?\S*?ibp=htl;jobs", None),  # Google job alerts
]
SENDER_DOMAINS = sorted({d for _, ds, _, _ in SITES for d in ds} - {"google.com"})
# Google sends many kinds of mail, so only its job-alert sender is searched.
EXTRA_SENDERS = ["notify-noreply@google.com"]

IMAP_PRESETS = {
    "imap.gmail.com": "Gmail (needs a Google app password)",
    "imap.qq.com": "QQ Mail (needs an authorization code)",
    "imap.163.com": "163 Mail (needs an authorization code)",
    "imap.126.com": "126 Mail (needs an authorization code)",
    "imap.mail.me.com": "iCloud Mail (needs an app-specific password)",
    "imap.mail.yahoo.com": "Yahoo Mail (needs an app password)",
    "imap.zoho.com": "Zoho Mail",
}

SKIP_TEXT = re.compile(r"unsubscribe|manage (your )?(alerts?|settings|notifications)|privacy|help center|terms|view (all|more)|"
                       r"see (all|more)|search (for )?(more|jobs)|download (the )?app|preferences|update your|learn more|"
                       r"^view in browser$|^(apply|view job|view|see job|open)$|premium|try for free|feedback", re.I)


def _address(sender):
    m = re.search(r"[\w.+-]+@[\w.-]+", sender or "")
    return m.group(0).lower() if m else ""


def _site_for(sender):
    addr = _address(sender)
    domain = addr.split("@")[-1]
    for name, domains, _, _ in SITES:
        if any(domain == d or domain.endswith("." + d) for d in domains):
            if name == "Google" and addr != "notify-noreply@google.com":
                continue
            return name
    return ""


ALERTISH = re.compile(r"job|intern|role|position|opportunit|recommend|alert|hiring|apply|career|employer|co-?op|"
                      r"职位|实习|工作|招聘", re.I)


def is_alert(sender, subject):
    """Only job alerts are read: LinkedIn also sends messages and connection mail, which we skip
    from the headers alone, before the body is fetched."""
    site, addr = _site_for(sender), _address(sender)
    if not site:
        return False
    if site == "LinkedIn":
        return "job" in addr.split("@")[0]
    return bool(ALERTISH.search(subject or "") or re.search(r"job|alert", addr.split("@")[0]))


def _unwrap(url):
    """Undo redirect wrappers (Outlook Safe Links, Google redirects, url= parameters)."""
    for _ in range(3):
        q = parse_qs(urlparse(url).query)
        inner = next((v[0] for k in ("url", "u", "redirect", "redirect_url", "target", "dest", "destination", "q")
                      for v in [q.get(k)] if v and v[0].startswith("http")), None)
        if not inner:
            break
        url = unquote(inner)
    return url.replace("&amp;", "&")


def _match(url):
    """(site, canonical link) when the link is a job posting we recognize."""
    for name, _, pattern, canon in SITES:
        m = re.search(pattern, url, re.I)
        if m:
            return name, (canon(m) if canon else m.group(0) if m.group(0).startswith("http") else "https://" + m.group(0))
    return "", ""


def _lines(text):
    return [l.strip() for l in re.split(r"[\n·•|]+", text or "") if l.strip()]


def _context(a, anchor_text, key, keys):
    """Text of the nearest block around a link (title, company, location usually sit together),
    stopping before the block grows to include another job's link."""
    node = a
    best = ""
    for _ in range(6):
        node = node.parent
        if node is None or node.name in ("body", "html", "[document]"):
            break
        if any(keys.get(id(x), key) != key for x in node.find_all("a", href=True)):
            break
        text = node.get_text("\n", strip=True)
        if len(text) > 600:
            break
        if len(text) > len(anchor_text) + 8:
            best = text
    return best or anchor_text


def candidates(html="", text=""):
    """Job-looking links from an email, grouped by job, with nearby text."""
    found, order = {}, []
    if html:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["style", "script", "head"]):
            tag.decompose()
        links, keys = [], {}
        for a in soup.find_all("a", href=True):
            href = _unwrap(a["href"].strip())
            if not href.startswith("http"):
                continue
            label = a.get_text(" ", strip=True)
            site, canon = _match(href)
            if not canon:
                # Tracking links (e.g. SendGrid on Handshake) hide the job link; keep links whose text looks like a role.
                if not (6 <= len(label) <= 120) or SKIP_TEXT.search(label) or not re.search(
                        r"intern|engineer|analyst|associate|developer|scientist|manager|designer|research|assistant|"
                        r"specialist|consult|co-?op|fellow|trainee|coordinator|representative|student", label, re.I):
                    continue
                canon = href
            key = canon.split("?")[0] if site else canon
            keys[id(a)] = key
            links.append((a, label, site, canon, key))
        for a, label, site, canon, key in links:
            if key not in found:
                found[key] = {"url": canon, "site": site, "texts": [], "context": ""}
                order.append(key)
            item = found[key]
            if label and label not in item["texts"] and not SKIP_TEXT.search(label):
                item["texts"].append(label)
            ctx = _context(a, label, key, keys)
            if len(ctx) > len(item["context"]):
                item["context"] = ctx
    if not order and text:
        lines = text.splitlines()
        for i, line in enumerate(lines):
            for raw in re.findall(r"https?://[^\s<>\"')\]]+", line):
                site, canon = _match(_unwrap(raw))
                if canon and canon not in found:
                    before = [l.strip() for l in lines[max(0, i - 5):i] if l.strip() and not l.strip().startswith("http")]
                    found[canon] = {"url": canon, "site": site, "texts": before[-1:], "context": "\n".join(before[-4:] + [line.replace(raw, "").strip()])}
                    order.append(canon)
    return [found[k] for k in order][:60]


ALERT_SYSTEM = """You read a job-alert email (from LinkedIn, Handshake, Indeed or a similar site) and list the job postings in it. Output a json object only.

You get numbered candidate links, each with its link text and the text around it. For each one that is a specific job posting, give its title, company and location exactly as the email shows them (don't invent anything; use "" when the email doesn't say). Skip links that aren't one specific job: searches, "see all jobs", settings, unsubscribe, courses, ads, profile views.

JSON shape:
{"jobs": [{"n": <candidate number>, "title": "", "company": "", "location": "", "salary": "", "posted": "<e.g. '2 days ago' or ''>", "note": "<anything useful the email says, e.g. 'Easy Apply', '10 applicants', 'actively recruiting', or ''>"}]}"""


def _guess(c):
    """Fallback without AI: link text as title, the next short lines as company and location."""
    title = max(c["texts"], key=len) if c["texts"] else ""
    rest = [l for l in _lines(c["context"]) if l != title and len(l) < 90 and not SKIP_TEXT.search(l)]
    location = next((l for l in rest if re.search(r",\s*[A-Z]{2}\b|remote|hybrid|on-?site|united states", l, re.I)), "")
    company = next((l for l in rest if l != location and not re.search(r"\d+ (days?|hours?|weeks?) ago|applicants?|\$", l, re.I)), "")
    return {"title": title, "company": company, "location": location}


def extract(subject, sender, html="", text=""):
    """Job postings in one alert email: [{title, company, location, url, site, ...}]."""
    cands = candidates(html, text)
    if not cands:
        return []
    site = _site_for(sender)
    if not site:  # pasted without headers: name the site most of the links point to
        sites = [c["site"] for c in cands if c["site"]]
        site = max(set(sites), key=sites.count) if sites else ""
    jobs = []
    try:
        listing = "\n\n".join(f"[{i + 1}] link text: {' / '.join(c['texts'])[:200]}\n    around it: {c['context'][:500]}"
                              for i, c in enumerate(cands))
        out = llm.chat_json([
            {"role": "system", "content": ALERT_SYSTEM},
            {"role": "user", "content": f"FROM: {sender}\nSUBJECT: {subject}\n\nCANDIDATES:\n{listing}"[:24000]},
        ], max_tokens=6000, temperature=0, think=False, timeout=120)
        for j in out.get("jobs") or []:
            try:
                c = cands[int(j.get("n")) - 1]
            except (TypeError, ValueError, IndexError):
                continue
            if j.get("title"):
                jobs.append({**{k: str(j.get(k) or "").strip() for k in ("title", "company", "location", "salary", "posted", "note")},
                             "url": c["url"], "site": c["site"] or site, "context": c["context"]})
    except llm.LLMError:
        for c in cands:
            if c["site"]:  # without AI, only trust links we recognize as job postings
                g = _guess(c)
                if g["title"]:
                    jobs.append({**g, "salary": "", "posted": "", "note": "", "url": c["url"], "site": c["site"], "context": c["context"]})
    return jobs


def _body(msg):
    html = text = ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        try:
            content = part.get_content()
        except (LookupError, KeyError, ValueError):
            payload = part.get_payload(decode=True) or b""
            content = payload.decode("utf-8", "replace")
        if ctype == "text/html" and not html:
            html = content
        elif ctype == "text/plain" and not text:
            text = content
    return html, text


def _date(msg):
    try:
        return parsedate_to_datetime(msg["Date"]).date().isoformat()
    except (TypeError, ValueError):
        return date.today().isoformat()


def _rows(jobs, received):
    rows = []
    for j in jobs:
        state, country, remote = taxonomy.parse_location(j["location"])
        desc = " · ".join(x for x in (j.get("salary"), j.get("posted"), j.get("note")) if x)
        rows.append({"id": "alert:" + hashlib.sha1(j["url"].encode()).hexdigest()[:16], "source": f"{j['site'] or 'Email'} alert",
                     "board": "email", "company": j["company"][:120], "domain": urlparse(j["url"]).netloc, "title": j["title"][:200],
                     "department": "", "function": taxonomy.classify_function(j["title"]),
                     "job_type": taxonomy.classify_job_type(j["title"]), "term": taxonomy.classify_term(j["title"]),
                     "location": j["location"][:200], "state": state, "country": country, "remote": int(remote), "industry": "",
                     "url": j["url"], "posted": received, "sponsorship": taxonomy.sponsorship_hint(j.get("context", "")),
                     "description": (desc + "\n\n" + j.get("context", ""))[:1500].strip(), "seen": date.today().isoformat()})
    return rows


def _save(rows):
    if not rows:
        return 0
    # Industries for new companies, using the same cache as the job-board crawler.
    from . import crawlers
    with db.connect() as con:
        known = {r["name"]: r["industry"] for r in con.execute("SELECT name, industry FROM companies WHERE industry != ''")}
        known.update({r["company"]: r["industry"] for r in con.execute(
            "SELECT company, industry FROM postings WHERE industry != '' AND board = 'email'")})
    new = [r["company"] for r in rows if r["company"] and r["company"] not in known]
    if new:
        known.update(crawlers._classify_industries(new))
    for r in rows:
        r["industry"] = known.get(r["company"], "")
    with db.connect() as con:
        db.upsert(con, "postings", rows)
    return len(rows)


def import_message(raw):
    """One pasted or uploaded alert (.eml bytes, or the email's text/HTML). Returns the postings added."""
    if isinstance(raw, bytes):
        raw_text = raw.decode("utf-8", "replace")
    else:
        raw_text = raw
    if re.search(r"^(From|Subject|Date|Received|MIME-Version):", raw_text[:3000], re.M):
        msg = email.message_from_bytes(raw if isinstance(raw, bytes) else raw_text.encode("utf-8"), policy=policy.default)
        html, text = _body(msg)
        subject, sender, received = str(msg["Subject"] or ""), str(msg["From"] or ""), _date(msg)
    else:
        looks_html = bool(re.search(r"<(a|table|div|html)\b", raw_text[:5000], re.I))
        html, text = (raw_text, "") if looks_html else ("", raw_text)
        subject, sender, received = "", "", date.today().isoformat()
    jobs = extract(subject, sender, html, text)
    if not jobs:
        raise AlertError("No job links found. Paste the whole alert email (or save it as .eml and upload it); "
                         "links to the jobs need to be included.")
    rows = _rows(jobs, received)
    _save(rows)
    return rows


# ---------- Inbox (IMAP, read-only) ----------

def configured():
    s = store.get_settings()
    return bool(s.get("alert_host") and s.get("alert_email") and s.get("alert_password"))


def _connect():
    s = store.get_settings()
    if not configured():
        raise AlertError("Add your email address and app password in Settings → Job alert emails.")
    host = s["alert_host"].strip()
    try:
        m = imaplib.IMAP4_SSL(host, 993, timeout=30)
    except OSError as e:
        raise AlertError(f"Couldn't reach {host} ({e.__class__.__name__}). Check the server name.")
    try:
        m.login(s["alert_email"].strip(), s["alert_password"].replace(" ", "") if "gmail" in host else s["alert_password"])
    except imaplib.IMAP4.error as e:
        raise AlertError("The mail server refused the login. Use an app password (Gmail, iCloud, Yahoo) or an authorization "
                         f"code (QQ, 163), not your normal password, and make sure IMAP is turned on. Server said: {e}")
    if host.endswith(("163.com", "126.com")):  # NetEase rejects clients that don't identify themselves
        imaplib.Commands.setdefault("ID", ("AUTH", "NONAUTH", "SELECTED"))
        try:
            m._simple_command("ID", '("name" "JobAgent" "version" "1.0")')
        except imaplib.IMAP4.error:
            pass
    return m


def _folder(m):
    """Gmail keeps archived and filtered alerts outside the inbox; search All Mail when it exists."""
    try:
        typ, lines = m.list()
        for line in lines or []:
            line = line.decode("utf-8", "replace") if isinstance(line, bytes) else str(line)
            if "\\All" in line:
                name = re.search(r'"([^"]+)"\s*$|(\S+)\s*$', line)
                return '"' + (name.group(1) or name.group(2)) + '"'
    except imaplib.IMAP4.error:
        pass
    return "INBOX"


def _search(m, days):
    since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
    ids = set()
    for sender in SENDER_DOMAINS + EXTRA_SENDERS:
        try:
            typ, data = m.uid("search", None, "SINCE", since, "FROM", f'"{sender}"')
        except imaplib.IMAP4.error:
            continue
        if typ == "OK" and data and data[0]:
            ids |= set(data[0].split())
    return sorted(ids, key=int)


def test_login():
    m = _connect()
    try:
        folder = _folder(m)
        m.select(folder, readonly=True)
        return len(_search(m, 30)), folder.strip('"')
    finally:
        try:
            m.logout()
        except imaplib.IMAP4.error:
            pass


def check_inbox(days=None, progress=None):
    """Read new alert emails from the last `days` days. Returns (postings saved, message)."""
    days = int(days or store.get_settings().get("alert_days") or 30)
    state = store.load("alerts", {"seen": [], "checked": ""})
    seen = set(state["seen"])
    m = _connect()
    rows, emails, errors = [], 0, 0
    try:
        m.select(_folder(m), readonly=True)  # read-only: nothing is marked read, moved or deleted
        uids = _search(m, days)
        for n, uid in enumerate(uids[-300:]):
            typ, data = m.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM SUBJECT)])")
            if typ != "OK" or not data or not isinstance(data[0], tuple):
                continue
            head = email.message_from_bytes(data[0][1], policy=policy.default)
            mid = str(head["Message-ID"] or "").strip() or uid.decode()
            if mid in seen or not is_alert(str(head["From"] or ""), str(head["Subject"] or "")):
                continue
            typ, data = m.uid("fetch", uid, "(BODY.PEEK[])")
            if typ != "OK" or not data or not isinstance(data[0], tuple):
                continue
            msg = email.message_from_bytes(data[0][1], policy=policy.default)
            sender = str(msg["From"] or "")
            if progress:
                progress(f"Reading alert {n + 1} of {len(uids)}…")
            html, text = _body(msg)
            try:
                rows += _rows(extract(str(msg["Subject"] or ""), sender, html, text), _date(msg))
            except Exception:  # one odd email shouldn't stop the rest
                errors += 1
                continue
            emails += 1
            seen.add(mid)
    finally:
        try:
            m.logout()
        except imaplib.IMAP4.error:
            pass
    unique = list({r["id"]: r for r in rows}.values())
    _save(unique)
    store.save("alerts", {"seen": list(seen)[-5000:], "checked": datetime.now().isoformat(timespec="seconds")})
    by_site = {}
    for r in unique:
        by_site[r["source"]] = by_site.get(r["source"], 0) + 1
    detail = ", ".join(f"{v} from {k.replace(' alert', '')}" for k, v in sorted(by_site.items(), key=lambda x: -x[1]))
    msg = f"{len(unique)} jobs from {emails} new alert emails" + (f" ({detail})" if detail else "") + (f"; {errors} couldn't be read" if errors else "")
    if not emails and not errors:
        msg = f"No new alert emails in the last {days} days"
    return len(unique), msg


def counts():
    with db.connect() as con:
        return {r["source"]: r["n"] for r in con.execute("SELECT source, COUNT(*) n FROM postings WHERE board = 'email' GROUP BY source")}
