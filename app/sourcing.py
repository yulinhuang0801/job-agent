"""Find firms and job postings to reach out to.

Sources:
- Web search (Tavily), using the "keyword + location + industry" search approach from the
  boutique internship guide, plus public pages on directories like SearchFunder and Axial.
- Public job boards (Greenhouse, Lever, Ashby) for actual postings.
- Lists the user imports (pasted names/URLs, or a CSV exported from Axial, Apollo, etc.).

LinkedIn is not crawled: it needs a login and its terms forbid automated scraping. Search
results that point to LinkedIn pages are still used as hints, and the Find tab links to
LinkedIn's own search so the user can browse it themselves.
"""
import csv
import io
import json
import re
from urllib.parse import urlparse

from . import llm, prompts, services, store

CATEGORIES = {
    "search_funds": {
        "label": "Search funds & ETA", "kind": "firm",
        "queries": ["search fund {loc}", "search fund acquiring {industry} business", "self-funded searcher {industry} acquisition {loc}",
                    "entrepreneurship through acquisition search fund {loc}", "site:searchfunder.com search fund {industry}"],
    },
    "small_pe": {
        "label": "Small private equity", "kind": "firm",
        "queries": ["lower middle market private equity firm {loc}", "{industry} private equity firm {loc}",
                    "independent sponsor {industry} {loc}", "small private equity firm {industry} {loc}"],
    },
    "boutique_ib": {
        "label": "Boutique investment banks", "kind": "firm",
        "queries": ["boutique investment bank {loc}", "{industry} investment bank boutique {loc}",
                    "M&A advisory boutique {industry} {loc}", "lower middle market investment bank {loc}"],
    },
    "small_vc": {
        "label": "Small VC firms", "kind": "firm",
        "queries": ["early stage venture capital firm {loc}", "{industry} seed venture capital fund {loc}",
                    "micro VC fund {industry} {loc}"],
    },
    "boutique_consulting": {
        "label": "Boutique consulting", "kind": "firm",
        "queries": ["boutique consulting firm {loc}", "boutique strategy consulting firm {industry} {loc}",
                    "{industry} consulting boutique {loc}"],
    },
    "family_offices": {
        "label": "Family offices", "kind": "firm",
        "queries": ["single family office {loc} investments", "family office direct investments {industry} {loc}"],
    },
    "postings": {
        "label": "Job postings (Greenhouse, Lever, Ashby)", "kind": "posting",
        "queries": ["site:boards.greenhouse.io {role} intern {loc}", "site:job-boards.greenhouse.io {role} intern {loc}",
                    "site:jobs.lever.co {role} intern {loc}", "site:jobs.ashbyhq.com {role} intern {loc}"],
    },
}

BIG_FIRMS = re.compile(r"\b(goldman|morgan stanley|jpmorgan|j\.p\. morgan|citi(group)?|bank of america|moelis|evercore|lazard|"
                       r"centerview|pjt|perella|houlihan|jefferies|blackstone|kkr|carlyle|apollo global|bain|mckinsey|bcg|"
                       r"deloitte|pwc|kpmg|ernst|sequoia|andreessen|a16z|accel|tiger global)\b", re.I)


def categories():
    return [{"id": k, "label": v["label"], "kind": v["kind"]} for k, v in CATEGORIES.items()]


def _queries(category, location, industry, role):
    out = []
    for q in CATEGORIES[category]["queries"]:
        q = q.format(loc=location or "", industry=industry or "", role=role or "")
        out.append(re.sub(r"\s+", " ", q).strip())
    return out


def _domain(url):
    try:
        host = urlparse(url if "://" in url else "https://" + url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _existing_domains():
    return {_domain(j.get("website") or j.get("url") or "") for j in store.load("jobs", [])} - {""}


def search(category, location="", industry="", role="", max_queries=4):
    """Run the category's searches and have the AI screen results into firm/posting candidates."""
    if category not in CATEGORIES:
        raise llm.LLMError("Unknown category.")
    if not services.search_enabled():
        raise llm.LLMError("Web search needs a Tavily key. Add it in Settings → Web lookups, then press Save settings.")
    if not role:
        targets = (store.load("profile", {}).get("targets") or {})
        role = (targets.get("roles") or ["software engineering"])[0][:40]
    results, seen = [], set()
    for q in _queries(category, location, industry, role)[:max_queries]:
        found = services.web_search({"query": q, "max_results": 10})
        for r in found.get("results", []):
            if r["url"] not in seen:
                seen.add(r["url"])
                results.append({**r, "query": q})
    if not results:
        return {"candidates": [], "queries": _queries(category, location, industry, role)[:max_queries]}

    cat = CATEGORIES[category]
    out = llm.chat_json([
        {"role": "system", "content": prompts.SCREEN_SYSTEM.format(category=cat["label"], kind=cat["kind"])},
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
            "source_url": c.get("source_url") or "", "category": category,
            "already_added": bool(dom and dom in have),
        })
    candidates.sort(key=lambda c: -c["fit_score"])
    return {"candidates": candidates, "queries": _queries(category, location, industry, role)[:max_queries]}


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
