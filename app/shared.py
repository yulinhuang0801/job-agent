"""Shared key: an invite link to a relay (relay/worker.js) that holds a friend's DeepSeek, Tavily, Serper and
Hunter keys and enforces daily limits for everyone together. Used only for services with no key of your own.

An invite looks like https://job-agent-relay.<name>.workers.dev/join/<code>. The code is sent as the bearer token;
the relay's chat endpoint speaks the OpenAI API, so llm.py only changes its base URL."""
import re

import httpx

from . import store

INVITE = re.compile(r"^(https://[A-Za-z0-9.-]+|http://(?:127\.0\.0\.1|localhost):\d+)/join/([a-z0-9]{8,64})/?$")
MODEL = "deepseek-flash"  # the cheapest model; the shared budget goes furthest with it


class SharedError(Exception):
    pass


def parse(invite):
    m = INVITE.match((invite or "").strip())
    return (m.group(1), m.group(2)) if m else None


def active():
    """(relay base URL, code), or None when no invite is saved."""
    return parse(store.get_settings().get("relay_invite", ""))


def headers():
    base_code = active()
    return {"authorization": f"Bearer {base_code[1]}"} if base_code else {}


def error_message(r):
    try:
        return r.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return f"The shared key answered {r.status_code}."


def post(path, payload, timeout=30):
    base, _ = active()
    r = httpx.post(base + path, json=payload, headers=headers(), timeout=timeout)
    if r.status_code != 200:
        raise SharedError(error_message(r))
    return r.json()


def get(path, params=None, timeout=20):
    base, _ = active()
    r = httpx.get(base + path, params=params or {}, headers=headers(), timeout=timeout)
    if r.status_code != 200:
        raise SharedError(error_message(r))
    return r.json()


def status():
    """Today's shared budget: {name, ai: {spent_cny, cap_cny, left_cny}, web_search, google_search, email_finder}."""
    if not active():
        raise SharedError("No invite saved.")
    try:
        return get("/status")
    except httpx.HTTPError as e:
        raise SharedError(f"Couldn't reach the shared key ({e.__class__.__name__}).")
