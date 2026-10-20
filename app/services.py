"""Optional outside services: web search (Tavily) and speech-to-text (any OpenAI-style
/audio/transcriptions endpoint, e.g. SiliconFlow SenseVoice, which works in China)."""
import io
import time
import wave

import httpx

from . import store


ASSEMBLYAI_URL = "https://api.assemblyai.com"


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
    if s["stt_provider"] == "assemblyai":
        return _assemblyai(content, s)
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


def _assemblyai(content, s):
    """AssemblyAI: upload the raw audio, start a transcript (auto-detecting English or Chinese), poll until done."""
    base = s["stt_base_url"].rstrip("/") if "assemblyai" in s["stt_base_url"] else ASSEMBLYAI_URL
    headers = {"authorization": s["stt_key"]}  # AssemblyAI takes the bare key, no "Bearer"
    try:
        up = httpx.post(f"{base}/v2/upload", headers=headers, content=content, timeout=60)
        if up.status_code == 401:
            raise ServiceError("AssemblyAI rejected the API key. Check it in Settings.")
        up.raise_for_status()
        job = httpx.post(f"{base}/v2/transcript", headers=headers, timeout=30,
                         json={"audio_url": up.json()["upload_url"], "language_detection": True})
        job.raise_for_status()
        tid = job.json()["id"]
        for _ in range(80):  # about two minutes
            time.sleep(1.5)
            r = httpx.get(f"{base}/v2/transcript/{tid}", headers=headers, timeout=30)
            r.raise_for_status()
            data = r.json()
            if data["status"] == "completed":
                return (data.get("text") or "").strip()
            if data["status"] == "error":
                raise ServiceError(f"AssemblyAI couldn't transcribe that: {data.get('error', 'unknown error')}")
    except httpx.HTTPError as e:
        raise ServiceError(f"Couldn't reach AssemblyAI ({e.__class__.__name__}).")
    raise ServiceError("AssemblyAI took too long. Try a shorter recording.")


def silent_wav(seconds=1):
    """A tiny silent recording, used to test a speech-to-text key."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000 * seconds)
    return buf.getvalue()


def hunter_enabled():
    return bool(store.get_settings()["hunter_key"])


def hunter_check():
    """Free account lookup (uses no credits). Returns searches left this month."""
    key = store.get_settings()["hunter_key"]
    if not key:
        raise ServiceError("No Hunter.io key saved.")
    try:
        r = httpx.get("https://api.hunter.io/v2/account", params={"api_key": key}, timeout=20)
    except httpx.HTTPError as e:
        raise ServiceError(f"Couldn't reach Hunter.io ({e.__class__.__name__}).")
    if r.status_code == 401:
        raise ServiceError("Hunter.io doesn't recognize this key. Copy it again from hunter.io → API keys.")
    if r.status_code != 200:
        raise ServiceError(f"Hunter.io error ({r.status_code}).")
    searches = ((r.json().get("data") or {}).get("requests") or {}).get("searches") or {}
    return searches.get("available", 0) - searches.get("used", 0)


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


def serper_enabled():
    return bool(store.get_settings()["serper_key"])


def serper_search(args):
    """Google results through Serper.dev. Same return shape as web_search."""
    query = str(args.get("query") or "").strip()
    n = int(args.get("max_results") or 10)
    try:
        r = httpx.post("https://google.serper.dev/search", timeout=20,
                       headers={"X-API-KEY": store.get_settings()["serper_key"], "Content-Type": "application/json"},
                       json={"q": query, "num": n})
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        return {"error": f"Google search failed ({e.__class__.__name__})."}
    return {"answer": "", "results": [{"title": o.get("title", ""), "url": o.get("link", ""), "content": (o.get("snippet") or "")[:500]}
                                      for o in data.get("organic", [])[:n]]}


def find_search(args):
    """Search for firms and postings: Google (Serper) when its key is saved, otherwise Tavily."""
    if serper_enabled():
        found = serper_search(args)
        if "error" not in found or not search_enabled():
            return found
    return web_search(args)


def engine_name():
    return "Google (Serper)" if serper_enabled() else "Tavily" if search_enabled() else ""
