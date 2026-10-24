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


# ---------- Finding the decision-maker (the Salt Creek research method) ----------
# Website first; then searches for the owner, founder or president; LinkedIn only through search results (Job Agent
# never logs into LinkedIn or Apollo). A person only counts if the source is about THIS firm (same name, place, field).

LINKEDIN_RE = re.compile(r"^https?://([a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9_%-]+/?$")
CONTACT_TYPES = ("Owner", "Founder", "Co-Owner", "Family Owner", "President", "CEO", "Managing Partner", "Partner", "Other Leadership")
PHONE_TYPES = ("Mobile", "Direct Line", "Main Office", "VoIP", "Not Found")


def people_searches(company, site):
    """[(query, results)]: who owns or leads the firm, from search engines (Serper or Tavily)."""
    if not company or not (services.serper_enabled() or services.search_enabled()):
        return []
    queries = [f'"{company}" founder OR owner OR "managing partner"']
    if not site or len(site.get("pages", [])) < 3:  # thin website: search harder
        queries.append(f'"{company}" president OR principal OR "managing director" OR CEO')
    out = []
    for q in queries:
        found = services.find_search({"query": q, "max_results": 6})
        results = [{"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")[:400]} for r in found.get("results", [])]
        if results:
            out.append((q, results))
    return out


def find_linkedin(name, company):
    """A LinkedIn profile URL from search results (never by visiting LinkedIn); '' when no result clearly matches."""
    if not (name and company and (services.serper_enabled() or services.search_enabled())):
        return ""
    found = services.find_search({"query": f'site:linkedin.com/in "{name}" "{company}"', "max_results": 5, "include_domains": ["linkedin.com"]})
    last = name.split()[-1].lower()
    for r in found.get("results", []):
        url = (r.get("url") or "").split("?")[0]
        text = f"{r.get('title', '')} {r.get('content', '')}".lower()
        if LINKEDIN_RE.search(url) and last in text and company.split()[0].lower() in text:
            return url
    return ""


def _drop_found(notes, linkedin, email):
    """The AI writes its notes before the LinkedIn and email lookups; drop the parts those lookups answered."""
    parts = [p.strip() for p in re.split(r"[;.]\s*", notes) if p.strip()]
    keep = [p for p in parts if not ((linkedin and "linkedin" in p.lower()) or (email and "email" in p.lower() and "not" in p.lower()))]
    return "; ".join(keep)


def email_confidence(email, how):
    """High: published or confirmed by two sources. Medium: a reliable database. Low: a guess or a general inbox."""
    if not email:
        return "Not Found"
    how = how or ""
    if "published" in how or "entered by you" in how or "pattern and Hunter" in how:
        return "High"
    if "Hunter" in how:
        return "Medium"
    return "Low"  # pattern guesses (including ones built from a known email) and general inboxes


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
        guess = f"{PATTERNS[pattern](first, last)}@{domain}" if pattern else ""
        found = services.hunter_find(domain, first, last)
        if found and found[0].lower() == guess:
            return guess, f"matches the firm's email pattern and Hunter.io (confidence {found[1]}%)"
        if found:
            return found[0], f"found by Hunter.io (confidence {found[1]}%)"
        if guess:
            return guess, f"guessed from the firm's email pattern ({pattern}); verify before sending"
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
    sources = [p["url"] for p in site["pages"]] if site else []
    for query, results in people_searches(job.get("company") or "", site):
        material.append(f"SEARCH RESULTS for {query}:\n" + json.dumps(results, ensure_ascii=False))
        sources += [r.get("url", "") for r in results]
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
    linkedin = next((p.get("linkedin") for p in people if p.get("name") == contact.get("name") and p.get("linkedin")), "")
    if contact.get("name") and not LINKEDIN_RE.search(linkedin or ""):
        linkedin = find_linkedin(contact["name"], job.get("company") or "")
    source = contact.get("source_url") or ""
    if source and source not in sources:
        source = ""  # only a page or search result we actually read counts as a source
    channel = out.get("channel") or "email"
    if job.get("kind") == "posting" and job.get("url"):
        channel = "form"

    job["website"] = website
    job["research"] = {k: out.get(k) for k in ("summary", "focus", "hooks", "size_estimate", "closed_deal", "fit_notes", "channel_reason")}
    job["research"]["people"] = people
    job["research"]["pages"] = [p["url"] for p in site["pages"]] if site else []
    job["research"]["emails_found"] = emails
    phone_type = contact.get("phone_type") if contact.get("phone_type") in PHONE_TYPES else ("Main Office" if contact.get("phone") else "Not Found")
    job["contact"] = {"name": contact.get("name", ""), "title": contact.get("title", ""), "why": contact.get("why", ""),
                      "email": email, "email_source": how, "email_confidence": email_confidence(email, how),
                      "contact_type": contact.get("contact_type") if contact.get("contact_type") in CONTACT_TYPES else ("Other Leadership" if contact.get("name") else ""),
                      "verified": contact.get("verified") if contact.get("verified") in ("Yes", "No", "Unclear") else ("Unclear" if contact.get("name") else ""),
                      "source_url": source, "linkedin": linkedin if LINKEDIN_RE.search(linkedin or "") else "",
                      "phone": contact.get("phone") or "", "phone_type": phone_type if contact.get("phone") else "Not Found",
                      "notes": _drop_found(str(contact.get("missing") or ""), linkedin, email)[:400]}
    job["channel"] = channel
    if out.get("apply_url") and not job.get("apply_url"):
        job["apply_url"] = out["apply_url"]
    return job


def email_from_example(job, known_email, known_name):
    """The user found one real address at the firm (on Apollo, Hunter, LinkedIn...). Learn its pattern
    and apply it to the contact, e.g. jwang@firm.com for Jason Wang → msmith@firm.com for Mary Smith."""
    known_email = known_email.strip().lower()
    contact = job.get("contact") or {}
    if "@" not in known_email:
        raise llm.LLMError("That doesn't look like an email address.")
    if not contact.get("name"):
        raise llm.LLMError("Add the contact's name first.")
    local, domain = known_email.split("@", 1)
    if local in GENERIC:
        raise llm.LLMError(f"{known_email} is a shared inbox, so it doesn't show how personal emails are formed. "
                           "Use a specific person's email.")
    pattern = detect_pattern([known_email], [{"name": known_name}], domain)
    if not pattern:
        raise llm.LLMError(f"Couldn't match {known_email} to the name \"{known_name}\". Check the spelling of the name.")
    first, last = _name_parts(contact["name"])
    if not first:
        raise llm.LLMError("Add the contact's full name (first and last).")
    contact["email"] = f"{PATTERNS[pattern](first, last)}@{domain}"
    contact["email_source"] = f"built from the {pattern} pattern of {known_email}; verify before sending"
    contact["email_confidence"] = "Low"
    job["contact"] = contact
    return job
