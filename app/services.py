"""Optional outside services: web search (Tavily) and speech-to-text (any OpenAI-style
/audio/transcriptions endpoint, e.g. SiliconFlow SenseVoice, which works in China)."""
import httpx

from . import store


class ServiceError(Exception):
    pass


SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web to understand something specific the candidate mentioned: a course code "
                       "(include the school name), a small company or startup, a lab, a competition or a niche tool.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query, e.g. 'Harvard CS 124 course'"}},
            "required": ["query"],
        },
    },
}


def search_enabled():
    return bool(store.get_settings()["search_key"])


def web_search(args):
    """Tool handler. Returns a compact result for the model; errors are returned, not raised,
    so the model can fall back to asking the candidate."""
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "Empty query."}
    key = store.get_settings()["search_key"]
    try:
        resp = httpx.post("https://api.tavily.com/search", timeout=20,
                          headers={"Authorization": f"Bearer {key}"},
                          json={"query": query, "search_depth": "basic", "max_results": int(args.get("max_results") or 4),
                                "include_answer": True})
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        return {"error": f"Search failed ({e.__class__.__name__}). Ask the candidate instead."}
    return {
        "answer": data.get("answer") or "",
        "results": [{"title": r.get("title", ""), "url": r.get("url", ""), "content": (r.get("content") or "")[:500]}
                    for r in data.get("results", [])[:int(args.get("max_results") or 4)]],
    }


def stt_enabled():
    return bool(store.get_settings()["stt_key"])


def transcribe(filename, content, content_type):
    s = store.get_settings()
    if not s["stt_key"]:
        raise ServiceError("No speech-to-text key. Add one in Settings, or use your browser's voice typing.")
    try:
        resp = httpx.post(f"{s['stt_base_url'].rstrip('/')}/audio/transcriptions", timeout=120,
                          headers={"Authorization": f"Bearer {s['stt_key']}"},
                          data={"model": s["stt_model"]},
                          files={"file": (filename, content, content_type or "application/octet-stream")})
    except httpx.HTTPError as e:
        raise ServiceError(f"Couldn't reach the speech-to-text service ({e.__class__.__name__}).")
    if resp.status_code != 200:
        raise ServiceError(f"Speech-to-text error ({resp.status_code}): {resp.text[:200]}")
    return (resp.json().get("text") or "").strip()


def hunter_enabled():
    return bool(store.get_settings()["hunter_key"])


def hunter_find(domain, first, last):
    """Hunter.io Email Finder: the most likely address for a person at a domain. Returns (email, score) or None."""
    key = store.get_settings()["hunter_key"]
    if not (key and domain and first and last):
        return None
    try:
        r = httpx.get("https://api.hunter.io/v2/email-finder", timeout=20,
                      params={"domain": domain, "first_name": first, "last_name": last, "api_key": key})
        r.raise_for_status()
        data = r.json().get("data") or {}
    except (httpx.HTTPError, ValueError):
        return None
    return (data["email"], data.get("score")) if data.get("email") else None


def reader_text(url):
    """Jina Reader renders JavaScript-heavy pages to plain text (free, rate-limited)."""
    try:
        r = httpx.get(f"https://r.jina.ai/{url}", timeout=30, headers={"Accept": "text/plain"})
        r.raise_for_status()
        return r.text
    except httpx.HTTPError:
        return ""
