"""Research a firm from its own website: what it does, hooks for a personal email, who to contact,
their email if it's published (or a likely email from the firm's email pattern), and whether to
apply through a form or by cold email."""
import json
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from . import llm, prompts, services
from .jobs_fetch import HEADERS

PAGE_WORDS = ("team", "people", "about", "leadership", "who-we-are", "our-firm", "partners", "professionals",
              "contact", "careers", "jobs", "join", "internship", "portfolio", "investments", "criteria", "approach")
MAX_PAGES = 6
MAX_TEXT = 30000
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
GENERIC = {"info", "contact", "hello", "admin", "office", "inquiries", "careers", "jobs", "team", "deals", "support", "privacy"}
ATS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workday", "bamboohr", "smartrecruiters", "jobvite", "breezy.hr", "rippling")


def _get(url):
    try:
        r = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=15)
        if r.status_code == 200 and "html" in r.headers.get("content-type", "html"):
            return str(r.url), r.text
    except httpx.HTTPError:
        pass
    return None, None


def _page_text(html):
    soup = BeautifulSoup(html, "html.parser")
    mailtos = [a["href"][7:].split("?")[0] for a in soup.select('a[href^="mailto:"]')]
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return soup, soup.get_text("\n", strip=True), mailtos


def crawl_site(website):
    """Fetch the home page plus the most useful linked pages on the same site."""
    if "://" not in website:
        website = "https://" + website
    base, html = _get(website)
    if not html:
        text = services.reader_text(website)
        if not text:
            return None
        host = urlparse(website).netloc
        return {"base": website, "domain": host[4:] if host.startswith("www.") else host, "pages": [{"url": website, "text": text[:15000]}],
                "emails": sorted(set(e.lower() for e in EMAIL_RE.findall(text))), "ats_links": []}
    host = urlparse(base).netloc
    soup, text, mailtos = _page_text(html)
    if len(text) < 300:  # page is built with JavaScript; get the rendered text instead
        text = services.reader_text(base) or text
    pages = [{"url": base, "text": text}]
    emails, ats_links = set(mailtos) | set(EMAIL_RE.findall(html)), set()
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"])
        if any(d in href for d in ATS):
            ats_links.add(href)
        label = (a.get_text(" ", strip=True) + " " + href).lower()
        if urlparse(href).netloc == host and any(w in label for w in PAGE_WORDS) and href.split("#")[0] != base:
            links.append(href.split("#")[0])
    for href in list(dict.fromkeys(links))[:MAX_PAGES - 1]:
        url, page = _get(href)
        if not page:
            continue
        psoup, ptext, pmail = _page_text(page)
        pages.append({"url": url, "text": ptext})
        emails |= set(pmail) | set(EMAIL_RE.findall(page))
        for a in psoup.find_all("a", href=True):
            if any(d in a["href"] for d in ATS):
                ats_links.add(urljoin(url, a["href"]))
    domain = host[4:] if host.startswith("www.") else host
    emails = sorted({e.lower().strip() for e in emails if "@" in e and not e.lower().endswith((".png", ".jpg", ".gif", ".webp"))})
    return {"base": base, "domain": domain, "pages": pages, "emails": emails, "ats_links": sorted(ats_links)}


def find_website(name):
    if not services.search_enabled():
        return ""
    found = services.web_search({"query": f"{name} official website", "max_results": 5})
    for r in found.get("results", []):
        host = urlparse(r["url"]).netloc.lower()
        if not any(s in host for s in ("linkedin.", "crunchbase.", "pitchbook.", "bloomberg.", "axial.net", "searchfunder.",
                                      "zoominfo.", "wikipedia.", "facebook.", "twitter.", "x.com", "glassdoor.", "indeed.")):
            return f"https://{host}"
    return ""


# ---------- Email patterns ----------

PATTERNS = {
    "first": lambda f, l: f,
    "first.last": lambda f, l: f"{f}.{l}",
    "firstlast": lambda f, l: f"{f}{l}",
    "flast": lambda f, l: f"{f[:1]}{l}",
    "f.last": lambda f, l: f"{f[:1]}.{l}",
    "firstl": lambda f, l: f"{f}{l[:1]}",
    "last": lambda f, l: l,
    "first_last": lambda f, l: f"{f}_{l}",
}


def _name_parts(name):
    parts = [re.sub(r"[^a-z]", "", p) for p in name.lower().replace("-", " ").split()]
    parts = [p for p in parts if p and p not in {"mr", "ms", "dr", "jr", "sr", "ii", "iii", "cfa", "mba"}]
    return (parts[0], parts[-1]) if len(parts) >= 2 else (None, None)


def detect_pattern(emails, people, domain):
    """Learn the firm's email format from any personal email on its site, e.g. jwang@ → first initial + last name."""
    for p in people:
        first, last = _name_parts(p.get("name", ""))
        if not first:
            continue
        for e in emails:
            local, _, dom = e.partition("@")
            if dom.endswith(domain):
                for pat, fn in PATTERNS.items():
                    if local == fn(first, last):
                        return pat
    return None


def contact_email(contact, people, emails, domain):
    """Return (email, how_we_know) for the primary contact."""
    name = (contact or {}).get("name", "")
    for p in people:
        if p.get("email") and p.get("name", "").lower() == name.lower():
            return p["email"], "published on their website"
    first, last = _name_parts(name)
    if first:
        for e in emails:
            local = e.split("@")[0]
            if local in {fn(first, last) for fn in PATTERNS.values()}:
                return e, "published on their website"
        pattern = detect_pattern(emails, people, domain)
        if pattern:
            return f"{PATTERNS[pattern](first, last)}@{domain}", f"guessed from the firm's email pattern ({pattern}); verify before sending"
    if first:
        found = services.hunter_find(domain, first, last)
        if found:
            return found[0], f"found by Hunter.io (confidence {found[1]}%)"
    generic = [e for e in emails if e.split("@")[0] in GENERIC and e.endswith(domain)]
    if generic:
        return generic[0], "general inbox on their website (a named person is better; try Apollo.io)"
    return "", "not found: look it up on Apollo.io, Hunter.io or RocketReach"


def research(job):
    """Fill in job['research'], job['contact'] and job['channel']."""
    website = job.get("website") or ""
    if not website:
        website = find_website(job.get("company") or job.get("title") or "")
    site = crawl_site(website) if website else None

    material = []
    if site:
        for p in site["pages"]:
            material.append(f"PAGE {p['url']}\n{p['text'][:8000]}")
        material.append("EMAILS FOUND ON THE SITE: " + (", ".join(site["emails"]) or "none"))
        material.append("APPLICATION / ATS LINKS FOUND: " + (", ".join(site["ats_links"]) or "none"))
    if services.search_enabled():
        found = services.web_search({"query": f"{job.get('company')} team founder managing partner", "max_results": 5})
        material.append("SEARCH RESULTS:\n" + json.dumps(found.get("results", []), ensure_ascii=False))
    if job.get("description"):
        material.append("JOB POSTING:\n" + job["description"][:6000])
    if not material:
        raise llm.LLMError("Couldn't find this firm's website. Add the website link on the card and try again.")

    out = llm.chat_json([
        {"role": "system", "content": prompts.RESEARCH_SYSTEM},
        {"role": "user", "content": f"FIRM: {job.get('company')}\nWEBSITE: {website}\n\n" + "\n\n".join(material)[:MAX_TEXT]},
    ], max_tokens=8000, temperature=0.2, timeout=150)

    people = [p for p in out.get("people") or [] if p.get("name")]
    emails = site["emails"] if site else []
    domain = site["domain"] if site else ""
    contact = out.get("primary_contact") or {}
    email, how = contact_email(contact, people, emails, domain) if contact.get("name") or emails else ("", "not found: look it up on Apollo.io, Hunter.io or RocketReach")
    channel = out.get("channel") or "email"
    if job.get("kind") == "posting" and job.get("url"):
        channel = "form"

    job["website"] = website
    job["research"] = {k: out.get(k) for k in ("summary", "focus", "hooks", "size_estimate", "closed_deal", "fit_notes", "channel_reason")}
    job["research"]["people"] = people
    job["research"]["pages"] = [p["url"] for p in site["pages"]] if site else []
    job["research"]["emails_found"] = emails
    job["contact"] = {"name": contact.get("name", ""), "title": contact.get("title", ""), "why": contact.get("why", ""),
                      "email": email, "email_source": how,
                      "linkedin": next((p.get("linkedin") for p in people if p.get("name") == contact.get("name") and p.get("linkedin")), "")}
    job["channel"] = channel
    if out.get("apply_url") and not job.get("apply_url"):
        job["apply_url"] = out["apply_url"]
    return job
