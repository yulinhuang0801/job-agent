"""Pull the text of a job posting from its URL. Works for most public career pages
(Greenhouse, Lever, Ashby, company sites); pages behind a login (LinkedIn, Handshake) won't."""
import json

import httpx
from bs4 import BeautifulSoup

MAX_CHARS = 15000
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}


class FetchError(Exception):
    pass


def _from_json_ld(soup):
    """Many career sites embed a schema.org JobPosting with the full description."""
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                desc = BeautifulSoup(item.get("description", ""), "html.parser").get_text("\n", strip=True)
                org = item.get("hiringOrganization") or {}
                return {
                    "title": item.get("title", ""),
                    "company": org.get("name", "") if isinstance(org, dict) else "",
                    "description": desc,
                }
    return None


def fetch_job(url):
    try:
        resp = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=20)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise FetchError(f"Couldn't open that page ({e.__class__.__name__}). Paste the job description instead.")

    soup = BeautifulSoup(resp.text, "html.parser")
    found = _from_json_ld(soup)
    if found and len(found["description"]) > 200:
        found["description"] = found["description"][:MAX_CHARS]
        return found

    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "svg", "form"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    if len(text) < 300:
        raise FetchError("That page didn't contain a readable job description (it may need a login). Paste the description instead.")
    title = soup.title.get_text(strip=True) if soup.title else ""
    return {"title": title, "company": "", "description": text[:MAX_CHARS]}
