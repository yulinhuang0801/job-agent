"""Tiny JSON-file storage. One local user for now; swap for a database when hosting."""
import json
import os
import threading
from pathlib import Path

DATA_DIR = Path(os.environ.get("JOB_AGENT_DATA", Path(__file__).resolve().parent.parent / "data"))
_lock = threading.Lock()

DEFAULT_SETTINGS = {
    "api_key": "",
    "model": "deepseek-flash",
    "base_url": "https://api.deepseek.com",
    "backup_key": "",  # used when the main model is down or overloaded
    "backup_base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "backup_model": "gemini-3.5-flash-lite",
    "search_key": "",  # Tavily
    "hunter_key": "",  # Hunter.io email finder
    "serper_key": "",  # Serper.dev Google results for the Find tab
    "sec_contact": "",  # SEC requires a contact email from automated downloads
    "dice_keywords": "",  # Find → Data sources → Dice: roles to search (blank = from your target roles)
    "dice_sponsor": "off",
    "automatch": "on",  # each morning: score new postings against the profile and prepare the best
    "automatch_prepare": "3",  # how many top matches to prepare each day (0-10)
    "automatch_depth": "docs",  # fit | docs (fit + tailored resume and cover letter) | full (also contact research)
    "automatch_min": "70",  # score a match needs to be prepared
    "discover_searches": "8",  # web searches per day for new company job boards (Serper or Tavily)  # only Dice employers willing to sponsor work authorization
    "alert_host": "imap.gmail.com",  # job-alert emails (LinkedIn, Handshake, Indeed...) read over IMAP, read-only
    "alert_email": "",
    "alert_password": "",  # an app password / authorization code, never the normal password
    "alert_days": "30",
    "alert_auto": "on",  # check every few hours while the app runs
    "stt_provider": "openai",  # "openai" = any OpenAI-style /audio/transcriptions API; or "assemblyai"
    "stt_base_url": "https://api.siliconflow.cn/v1",
    "stt_key": "",
    "stt_model": "FunAudioLLM/SenseVoiceSmall",
}


def _path(name):
    return DATA_DIR / f"{name}.json"


def load(name, default):
    p = _path(name)
    if not p.exists():
        return default
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def save(name, value):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    p = _path(name)
    tmp = p.with_suffix(".tmp")
    with _lock:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    if name == "settings":
        os.chmod(p, 0o600)  # holds the API key


def get_settings():
    return {**DEFAULT_SETTINGS, **load("settings", {})}
