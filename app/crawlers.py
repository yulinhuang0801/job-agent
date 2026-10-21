"""Fill the local database from public sources. Each import runs in a background thread and reports
progress in the `sources` table.

- SEC Form ADV (monthly): registered and exempt-reporting investment advisers, i.e. PE, VC, hedge
  fund and real-estate managers and wealth managers, with employee counts, private fund types,
  assets and websites. Exempt reporting advisers are mostly small private fund managers.
- SEC Form D (quarterly): new private funds and startups that just raised money, with the people
  named on the filing (executive officers, directors, promoters).
- Company job boards (Greenhouse, Lever, Ashby public APIs) and the SimplifyJobs internship lists.

SEC asks automated tools to identify themselves with a contact email (Settings → SEC contact email).
"""
import csv
import hashlib
import html as html_lib
import io
import json
import re
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from . import db, llm, store, taxonomy

ADV_PAGE = "https://www.sec.gov/data-research/sec-markets-data/information-about-registered-investment-advisers-exempt-reporting-advisers"
FORM_D_PAGE = "https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets"
SIMPLIFY = "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/"
SKIP_SITES = ("facebook.", "linkedin.", "twitter.", "x.com", "instagram.", "youtube.")
_running = set()
_lock = threading.Lock()


class CrawlError(Exception):
    pass


def run_in_background(name, fn, *args):
    with _lock:
        if name in _running:
            raise CrawlError(f"{name} is already updating.")
        _running.add(name)

    def work():
        db.set_source(name, "running", "Starting…")
        try:
            rows, message = fn(*args)
            db.set_source(name, "done", message, rows)
        except Exception as e:  # report any failure to the page instead of dying silently
            db.set_source(name, "error", (str(e) if isinstance(e, CrawlError) else f"{e.__class__.__name__}: {e}")[:300])
        finally:
            with _lock:
                _running.discard(name)
    threading.Thread(target=work, daemon=True).start()


def running():
    return sorted(_running)


def _progress(name, message):
    db.set_source(name, "running", message)


# ---------- SEC downloads ----------

def _sec_client():
    contact = store.get_settings().get("sec_contact", "").strip()
    if "@" not in contact:
        raise CrawlError("SEC requires a contact email for downloads. Add it in Settings → SEC contact email.")
    return httpx.Client(headers={"User-Agent": f"Job Agent {contact}"}, timeout=httpx.Timeout(30, read=120), follow_redirects=True)


def _sec_links(client, page, pattern):
    r = client.get(page)
    r.raise_for_status()
    links = []
    for a in BeautifulSoup(r.text, "html.parser").find_all("a", href=True):
        href = a["href"]
        if re.search(pattern, href):
            links.append(href if href.startswith("http") else "https://www.sec.gov" + href)
    return list(dict.fromkeys(links))


def _zip_csv(content):
    z = zipfile.ZipFile(io.BytesIO(content))
    name = z.namelist()[0]
    return list(csv.DictReader(io.StringIO(z.read(name).decode("latin-1"))))


def _num(v):
    try:
        return int(float(str(v).replace(",", "").replace("$", "").strip()))
    except (TypeError, ValueError):
        return None


def _site(url):
    url = (url or "").strip()
    if not url or any(s in url.lower() for s in SKIP_SITES):
        return "", ""
    if not url.lower().startswith("http"):
        url = "https://" + url
    host = urlparse(url.lower()).netloc
    return url.lower(), host[4:] if host.startswith("www.") else host


_SUFFIXES = {"Llc": "LLC", "L.L.C.": "LLC", "Lp": "LP", "L.P.": "L.P.", "Llp": "LLP", "Inc": "Inc", "Ii": "II", "Iii": "III",
             "Iv": "IV", "Usa": "USA", "Us": "US", "Gp": "GP", "Ltd": "Ltd", "Nyc": "NYC", "Ny": "NY", "Pe": "PE", "Vc": "VC"}


def _title(s):
    """SEC data is often ALL CAPS; make it readable without mangling LLC, LP, II..."""
    s = (s or "").strip()
    if not s.isupper():
        return s
    words = s.title().split()
    return " ".join(_SUFFIXES.get(w.rstrip(",."), w.rstrip(",.")) + (w[len(w.rstrip(",.")):] if w != w.rstrip(",.") else "") for w in words)


# ---------- Form ADV ----------

FUND_FLAGS = [("Any PE Funds", "Private equity", "Private Equity"), ("Any VC Funds", "Venture capital", "Venture Capital"),
              ("Any Real Estate Funds", "Real estate", "Real Estate"), ("Any Hedge Funds", "Hedge fund", "Hedge Funds & Asset Management"),
              ("Any Other Funds", "Other private funds", "Hedge Funds & Asset Management")]


def import_adv():
    name = "SEC Form ADV"
    with _sec_client() as client:
        links = _sec_links(client, ADV_PAGE, r"information-about-registered-investment-advisers-exempt-reporting-advisers/.+\.zip$")
        # Newest first on the page; the folder name itself contains "exempt", so look at file names only.
        registered = next((l for l in links if "exempt" not in l.rsplit("/", 1)[-1]), None)
        exempt = next((l for l in links if "exempt" in l.rsplit("/", 1)[-1]), None)
        if not registered or not exempt:
            raise CrawlError("Couldn't find the latest Form ADV files on sec.gov.")
        rows_out = []
        for url, is_exempt in ((registered, False), (exempt, True)):
            _progress(name, f"Downloading {url.rsplit('/', 1)[-1]}…")
            r = client.get(url)
            r.raise_for_status()
            for row in _zip_csv(r.content):
                status = (row.get("SEC Current Status") or "").upper()
                if status and not any(w in status for w in ("ACTIVE", "APPROV")):  # e.g. "Approved", "120-Day Approval", "ERA - Active"
                    continue
                flags = [(cat, ind) for col, cat, ind in FUND_FLAGS if (row.get(col) or "").strip().upper() in ("Y", "YES")]
                category, industry = flags[0] if flags else ("Wealth management / advisory", "Wealth Management")
                employees = _num(row.get("5A"))
                assets = _num(row.get("Total Gross Assets of Private Funds")) or _num(row.get("5F(2)(c)"))
                website, domain = _site(row.get("Website Address"))
                small = employees <= 30 if employees is not None else (assets is not None and assets < 300_000_000)
                rows_out.append({
                    "id": f"adv:{row.get('Organization CRD#') or row.get('SEC#')}", "source": name,
                    "name": _title(row.get("Primary Business Name")), "legal_name": _title(row.get("Legal Name")),
                    "kind": "adviser", "industry": industry, "category": category,
                    "city": _title(row.get("Main Office City")), "state": (row.get("Main Office State") or "").strip(),
                    "country": _title(row.get("Main Office Country")) or "United States",
                    "website": website, "domain": domain, "phone": (row.get("Main Office Telephone Number") or "").strip(),
                    "employees": employees, "size": taxonomy.size_bucket(employees), "assets": assets, "small": int(bool(small)),
                    "fund_types": ", ".join(c for c, _ in flags), "filed": (row.get("Latest ADV Filing Date") or "")[:10],
                    "founded": "", "raised": None,
                    "extra": {"exempt_reporting_adviser": is_exempt, "private_funds": _num(row.get("Count of Private Funds - 7B(1)")),
                              "sec_number": row.get("SEC#", ""), "crd": row.get("Organization CRD#", "")},
                })
        _progress(name, f"Saving {len(rows_out):,} advisers…")
        with db.connect() as con:
            con.execute("DELETE FROM firms WHERE source = ?", (name,))
            for i in range(0, len(rows_out), 2000):
                db.upsert(con, "firms", rows_out[i:i + 2000])
    return len(rows_out), f"{len(rows_out):,} advisers from {registered.rsplit('/', 1)[-1]} and {exempt.rsplit('/', 1)[-1]}"


# ---------- Form D ----------

def import_form_d(quarters=4):
    name = "SEC Form D"
    with _sec_client() as client:
        links = [l for l in _sec_links(client, FORM_D_PAGE, r"form-d-data-sets/\d{4}q\d_d\.zip$")]
        links.sort(key=lambda l: re.search(r"(\d{4}q\d)", l).group(1), reverse=True)
        if not links:
            raise CrawlError("Couldn't find Form D data sets on sec.gov.")
        firms, people = {}, {}
        big = _big_managers()
        for url in links[:quarters]:
            label = re.search(r"(\d{4}q\d)", url).group(1)
            _progress(name, f"Downloading {label}…")
            r = client.get(url)
            r.raise_for_status()
            z = zipfile.ZipFile(io.BytesIO(r.content))
            folder = z.namelist()[0].split("/")[0]

            def table(t):
                return csv.DictReader(io.StringIO(z.read(f"{folder}/{t}.tsv").decode("latin-1")), delimiter="\t")
            filed = {s["ACCESSIONNUMBER"]: s["FILING_DATE"] for s in table("FORMDSUBMISSION")}
            offering = {o["ACCESSIONNUMBER"]: o for o in table("OFFERING")}
            persons = {}
            for p in table("RELATEDPERSONS"):
                full = " ".join(x for x in (p["FIRSTNAME"], p["MIDDLENAME"], p["LASTNAME"]) if x and x.strip() and x.strip() != "-").strip()
                if full:
                    role = ", ".join(x for x in (p["RELATIONSHIP_1"], p["RELATIONSHIP_2"], p["RELATIONSHIP_3"]) if x)
                    persons.setdefault(p["ACCESSIONNUMBER"], []).append((_title(full), role + (f" ({p['RELATIONSHIPCLARIFICATION']})" if p.get("RELATIONSHIPCLARIFICATION") else "")))
            for iss in table("ISSUERS"):
                if iss["IS_PRIMARYISSUER_FLAG"] != "YES":
                    continue
                acc = iss["ACCESSIONNUMBER"]
                off = offering.get(acc, {})
                date = _sec_date(filed.get(acc, ""))
                fid = f"formd:{iss['CIK']}"
                if fid in firms and firms[fid]["filed"] >= date:
                    continue  # keep each issuer's latest filing
                is_fund = off.get("INDUSTRYGROUPTYPE") == "Pooled Investment Fund"
                fund_type = off.get("INVESTMENTFUNDTYPE", "")
                if is_fund:
                    industry = taxonomy.FORM_D_FUND_TYPES.get(fund_type, "Hedge Funds & Asset Management")
                    category = f"New fund: {fund_type or 'investment fund'}"
                else:
                    group = off.get("INDUSTRYGROUPTYPE", "Other")
                    industry = taxonomy.FORM_D_INDUSTRY.get(group, "Other")
                    category = f"Raised money: {group}"
                entity = _title(iss["ENTITYNAME"])
                if re.search(r"\bsearch\b", entity, re.I) and (not is_fund or fund_type in ("Private Equity Fund", "Other Investment Fund")):
                    category, industry = "Possible search fund", "Private Equity"
                state_or_country = iss.get("STATEORCOUNTRY", "")
                firms[fid] = {
                    "id": fid, "source": name, "name": entity, "legal_name": entity, "kind": "fund" if is_fund else "startup",
                    "industry": industry, "category": category, "city": _title(iss.get("CITY")),
                    "state": state_or_country if state_or_country in taxonomy.US_STATES else "",
                    "country": "United States" if state_or_country in taxonomy.US_STATES else _title(iss.get("STATEORCOUNTRYDESCRIPTION")),
                    "website": "", "domain": "", "phone": iss.get("ISSUERPHONENUMBER", ""), "employees": None, "size": "",
                    # Startups are small; funds count as small only if they raised under $100M and their name
                    # doesn't start like a big registered manager's (e.g. "Third Point ... Fund").
                    "assets": None, "small": int(not is_fund or ((_num(off.get("TOTALAMOUNTSOLD")) or 0) < 100_000_000
                                                                 and _prefix(entity) not in big)),
                    "fund_types": fund_type, "filed": date,
                    "founded": iss.get("YEAROFINC_VALUE_ENTERED", ""), "raised": _num(off.get("TOTALAMOUNTSOLD")),
                    "extra": {"cik": iss["CIK"], "accession": acc, "offering_amount": off.get("TOTALOFFERINGAMOUNT", ""),
                              "filing_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={iss['CIK']}&type=D"},
                }
                people[fid] = persons.get(acc, [])[:8]
            time.sleep(0.5)  # be polite to sec.gov
        _progress(name, f"Saving {len(firms):,} funds and companies…")
        rows = list(firms.values())
        with db.connect() as con:
            con.execute("DELETE FROM people WHERE source = ?", (name,))
            con.execute("DELETE FROM firms WHERE source = ?", (name,))
            for i in range(0, len(rows), 2000):
                db.upsert(con, "firms", rows[i:i + 2000])
            con.executemany("INSERT INTO people VALUES (?,?,?,?)",
                            [(fid, n, t, name) for fid, ps in people.items() for n, t in ps])
    return len(rows), f"{len(rows):,} funds and companies from {quarters} quarters, with {sum(len(p) for p in people.values()):,} people"


def _prefix(name):
    words = re.findall(r"[a-z0-9]+", name.lower())
    words = [w for w in words if w not in ("the", "fund", "funds", "capital", "partners", "management", "llc", "lp", "l", "p", "inc")]
    return " ".join(words[:2])


def _big_managers():
    """Name prefixes of registered advisers with over 100 employees (from Form ADV, if downloaded)."""
    with db.connect() as con:
        return {_prefix(r["name"]) for r in con.execute("SELECT name FROM firms WHERE source = 'SEC Form ADV' AND employees > 100")} - {""}


def _sec_date(s):
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return s[:10]


# ---------- Job boards ----------

BOARD_PATTERNS = {
    "greenhouse": r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_app\?for=)?([A-Za-z0-9_-]+)",
    "lever": r"jobs\.lever\.co/([A-Za-z0-9_.-]+)",
    "ashby": r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)",
}


def board_from_url(url):
    for board, pattern in BOARD_PATTERNS.items():
        m = re.search(pattern, url or "")
        if m and m.group(1) not in ("embed", "v1", "jobs"):
            return board, m.group(1)
    return None, None


def _text(html_or_text, n=3000):
    return re.sub(r"\s+", " ", BeautifulSoup(html_or_text or "", "html.parser").get_text(" ")).strip()[:n]


def _fetch_board(board, token):
    """Return (company name, postings) from one public job board."""
    name = unquote(token).replace("-", " ").replace("_", " ").strip().title()
    out = []
    if board == "greenhouse":
        r = httpx.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", params={"content": "true"}, timeout=40)
        r.raise_for_status()
        for j in r.json().get("jobs", []):
            name = j.get("company_name") or name
            out.append({"id": f"gh:{token}:{j['id']}", "title": j["title"], "department": ", ".join(d["name"] for d in j.get("departments", [])),
                        "location": (j.get("location") or {}).get("name", ""), "url": j.get("absolute_url", ""),
                        "posted": (j.get("first_published") or j.get("updated_at") or "")[:10],
                        "description": _text(html_lib.unescape(j.get("content") or "")), "commitment": ""})
    elif board == "lever":
        r = httpx.get(f"https://api.lever.co/v0/postings/{token}", params={"mode": "json"}, timeout=40)
        r.raise_for_status()
        for j in r.json():
            c = j.get("categories") or {}
            loc = c.get("location") or ", ".join(c.get("allLocations") or [])
            if j.get("workplaceType") == "remote":
                loc = f"Remote; {loc}" if loc else "Remote"
            out.append({"id": f"lever:{token}:{j['id']}", "title": j["text"], "department": " / ".join(x for x in (c.get("department"), c.get("team")) if x),
                        "location": loc, "url": j.get("hostedUrl", ""),
                        "posted": datetime.fromtimestamp(j["createdAt"] / 1000).date().isoformat() if j.get("createdAt") else "",
                        "description": (j.get("descriptionPlain") or "")[:3000], "commitment": c.get("commitment", "")})
    elif board == "ashby":
        r = httpx.get(f"https://api.ashbyhq.com/posting-api/job-board/{token}", timeout=40)
        r.raise_for_status()
        for j in r.json().get("jobs", []):
            if j.get("isListed") is False:
                continue
            loc = j.get("location") or ""
            if j.get("isRemote") and "remote" not in loc.lower():
                loc = f"Remote; {loc}" if loc else "Remote"
            out.append({"id": f"ashby:{token}:{j['id']}", "title": j["title"], "department": " / ".join(x for x in (j.get("department"), j.get("team")) if x),
                        "location": loc, "url": j.get("jobUrl", ""), "posted": (j.get("publishedAt") or "")[:10],
                        "description": (j.get("descriptionPlain") or "")[:3000], "commitment": j.get("employmentType", "")})
    return name, out


def _posting_row(p, company, board, source, industry=""):
    state, country, remote = taxonomy.parse_location(p["location"])
    return {"id": p["id"], "source": source, "board": board, "company": company, "domain": urlparse(p["url"]).netloc,
            "title": p["title"], "department": p["department"], "function": taxonomy.classify_function(p["title"], p["department"]),
            "job_type": taxonomy.classify_job_type(p["title"], p.get("commitment", "")),
            "term": p.get("term") or taxonomy.classify_term(p["title"]), "location": p["location"][:200], "state": state,
            "country": country, "remote": int(remote), "industry": industry, "url": p["url"], "posted": p.get("posted", ""),
            "sponsorship": p.get("sponsorship") or taxonomy.sponsorship_hint(p.get("description", "")),
            "description": p.get("description", ""), "seen": datetime.now().date().isoformat()}


def _simplify_postings():
    """Every open role in SimplifyJobs' summer and off-season lists, plus the job-board tokens in their links."""
    rows, tokens = [], set()
    for filename, season in (("README.md", "Summer 2027"), ("README-Off-Season.md", "")):
        r = httpx.get(SIMPLIFY + filename, timeout=httpx.Timeout(20, read=90))
        r.raise_for_status()
        for board, pattern in BOARD_PATTERNS.items():
            tokens |= {(board, t) for t in re.findall(pattern, r.text) if t not in ("embed", "v1", "jobs")}
        company = ""
        for part in re.split(r"\n(?=## )", r.text):
            if "<table" not in part:
                continue
            for tr in BeautifulSoup(part, "html.parser").find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) < 4:
                    continue
                raw_company = tds[0].get_text(" ", strip=True)
                company = company if raw_company.startswith("↳") else re.sub(r"^[^\w(]+", "", raw_company).strip()
                apply = next((a["href"] for a in tr.find_all("a", href=True) if a.find("img") and a.find("img").get("alt") == "Apply"), "")
                if not apply:
                    continue  # closed
                role = tds[1].get_text(" ", strip=True)
                flags = role + raw_company
                sponsorship = "US citizens only" if "🇺🇸" in flags else "No sponsorship" if "🛂" in flags else ""
                term = tds[3].get_text(" ", strip=True) if len(tds) >= 6 else season
                loc = "; ".join(t.strip() for t in tds[2].get_text("\n", strip=True).split("\n") if t.strip() and not t.strip().endswith("locations"))
                pid = "simplify:" + hashlib.sha1(apply.encode()).hexdigest()[:16]
                rows.append({"id": pid, "title": re.sub(r"[^\w)\]]+$", "", role).strip(), "department": "", "location": loc, "url": apply,
                             "posted": "", "description": "", "commitment": "Internship", "term": term, "sponsorship": sponsorship,
                             "company": company})
    return rows, tokens


def _classify_industries(names):
    """One AI call per ~120 company names; results are cached in the companies table."""
    out = {}
    names = sorted(set(n for n in names if n))
    for i in range(0, len(names), 120):
        batch = names[i:i + 120]
        try:
            res = llm.chat_json([
                {"role": "system", "content": "Classify each company into exactly one industry from this list: "
                                              + "; ".join(taxonomy.INDUSTRIES) + ". If unsure, use Other. Output a json object "
                                              "{\"industries\": {\"<company name exactly as given>\": \"<industry>\"}}."},
                {"role": "user", "content": json.dumps(batch, ensure_ascii=False)},
            ], max_tokens=8000, temperature=0, think=False, timeout=120)
            for k, v in (res.get("industries") or {}).items():
                if v in taxonomy.INDUSTRIES:
                    out[k] = v
        except llm.LLMError:
            continue
    return out


def crawl_jobs():
    name = "Job boards"
    _progress(name, "Reading the SimplifyJobs lists…")
    simplify, tokens = _simplify_postings()
    with db.connect() as con:
        tokens |= {(r["board"], r["token"]) for r in con.execute("SELECT board, token FROM companies")}
        known = {r["name"]: r["industry"] for r in con.execute("SELECT name, industry FROM companies WHERE industry != ''")}
    tokens = sorted(tokens)
    results, done = {}, 0

    def one(bt):
        try:
            return bt, _fetch_board(*bt), ""
        except (httpx.HTTPError, ValueError) as e:
            return bt, (None, []), e.__class__.__name__
    with ThreadPoolExecutor(max_workers=8) as pool:
        for bt, (company, posts), err in pool.map(one, tokens):
            results[bt] = (company, posts, err)
            done += 1
            if done % 20 == 0:
                _progress(name, f"Read {done}/{len(tokens)} company job boards…")

    companies = {c for c, _, _ in results.values() if c} | {p["company"] for p in simplify}
    missing = [c for c in companies if c not in known]
    if missing:
        _progress(name, f"Sorting {len(missing)} companies into industries…")
        known.update(_classify_industries(missing))

    rows = []
    for (board, token), (company, posts, err) in results.items():
        rows += [_posting_row(p, company, board, "Company job boards", known.get(company, "")) for p in posts]
    seen = {r["url"] for r in rows}
    rows += [_posting_row(p, p["company"], "", "SimplifyJobs list", known.get(p["company"], "")) for p in simplify if p["url"] not in seen]
    with db.connect() as con:
        # Replace only this crawl's rows; Dice and job-alert emails keep theirs.
        con.execute("DELETE FROM postings WHERE source IN ('Company job boards', 'SimplifyJobs list')")
        for i in range(0, len(rows), 2000):
            db.upsert(con, "postings", rows[i:i + 2000])
        now = datetime.now().isoformat(timespec="seconds")
        db.upsert(con, "companies", [{"board": b, "token": t, "name": c or t, "industry": known.get(c or "", ""), "website": "",
                                      "jobs": len(p), "status": e or "ok", "checked": now} for (b, t), (c, p, e) in results.items()])
    failed = sum(1 for _, _, e in results.values() if e)
    return len(rows), f"{len(rows):,} postings from {len(tokens) - failed} company job boards and the SimplifyJobs lists" + (f" ({failed} boards failed)" if failed else "")


def add_board(url):
    board, token = board_from_url(url)
    if not board:
        raise CrawlError("Paste a Greenhouse, Lever or Ashby job board link (e.g. https://jobs.lever.co/company).")
    company, posts = _fetch_board(board, token)
    with db.connect() as con:
        db.upsert(con, "companies", [{"board": board, "token": token, "name": company, "industry": "", "website": "", "jobs": len(posts),
                                      "status": "ok", "checked": datetime.now().isoformat(timespec="seconds")}])
        db.upsert(con, "postings", [_posting_row(p, company, board, "Company job boards") for p in posts])
    return {"company": company, "postings": len(posts)}
