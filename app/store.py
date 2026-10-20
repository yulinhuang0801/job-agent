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
