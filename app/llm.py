"""Model calls. The main provider is DeepSeek; an optional backup (Google Gemini by default)
is used automatically when the main one is down, overloaded or too slow. Both speak the
OpenAI-compatible API, so either can be swapped for another provider in Settings."""
import json
import logging
import re

import openai

from . import store

log = logging.getLogger("uvicorn.error")


class LLMError(Exception):
    pass


class _TryNext(Exception):
    """This provider failed in a way the backup might not (outage, rate limit, timeout)."""


def _providers():
    s = store.get_settings()
    providers = []
    if s["api_key"]:
        providers.append({"name": "DeepSeek", "key": s["api_key"], "base_url": s["base_url"], "model": s["model"]})
    if s["backup_key"]:
        providers.append({"name": "Backup", "key": s["backup_key"], "base_url": s["backup_base_url"], "model": s["backup_model"]})
    if not providers:
        raise LLMError("No API key yet. Add your DeepSeek API key on the Settings page.")
    return providers


def _call_one(p, messages, max_tokens, json_mode, temperature, tools, think, timeout):
    client = openai.OpenAI(api_key=p["key"], base_url=p["base_url"], timeout=timeout, max_retries=1)
    kwargs = {"model": p["model"], "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if tools:
        kwargs["tools"] = tools
    if "deepseek" in p["base_url"]:
        # DeepSeek thinks before answering by default; skip it for plain extraction work.
        kwargs["extra_body"] = {"thinking": {"type": "enabled" if think else "disabled"}}
    elif "googleapis" in p["base_url"]:
        # Gemini thinks at length by default (30s+ even for short replies); keep the backup quick.
        kwargs["reasoning_effort"] = "low"
    try:
        resp = client.chat.completions.create(**kwargs)
    except openai.AuthenticationError:
        raise LLMError(f"{p['name']} rejected the API key. Check it on the Settings page.")
    except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError) as e:
        raise _TryNext(f"{p['name']}: {e.__class__.__name__}")
    except openai.APIStatusError as e:
        if e.status_code >= 500 or e.status_code == 429:
            raise _TryNext(f"{p['name']}: HTTP {e.status_code}")
        raise LLMError(f"{p['name']} API error ({e.status_code}): {e.message}")
    choice = resp.choices[0]
    if choice.finish_reason == "length":
        raise _TryNext(f"{p['name']}: answer was cut off at {max_tokens} tokens")
    return choice.message


def _call(messages, max_tokens, json_mode, temperature, tools=None, think=True, timeout=90):
    """Return the model's message object, falling back to the backup provider if needed."""
    problems = []
    for p in _providers():
        try:
            msg = _call_one(p, messages, max_tokens, json_mode, temperature, tools, think, timeout)
            if problems:
                log.warning("Used backup model after: %s", "; ".join(problems))
            return msg
        except _TryNext as e:
            problems.append(str(e))
    has_backup = len(_providers()) > 1
    raise LLMError("The AI service is busy or unreachable right now (" + "; ".join(problems) + "). "
                   + ("Please try again in a minute." if has_backup
                      else "Try again in a minute, or add a backup AI key in Settings."))


def test_provider(which):
    """Send a tiny request to one provider ("main" or "backup"), with no fallback."""
    providers = {("main" if p["name"] == "DeepSeek" else "backup"): p for p in _providers()}
    p = providers.get(which)
    if not p:
        raise LLMError("No key saved for that model yet.")
    try:
        msg = _call_one(p, [{"role": "user", "content": "Reply with just the word: connected"}],
                        max_tokens=200, json_mode=False, temperature=0, tools=None, think=False, timeout=30)
    except _TryNext as e:
        raise LLMError(f"Couldn't get an answer ({e}).")
    return p["name"], p["model"], (msg.content or "").strip()


def _parse_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def chat(messages, max_tokens=2000, temperature=0.7, timeout=60):
    return (_call(messages, max_tokens, json_mode=False, temperature=temperature, think=False, timeout=timeout).content or "").strip()


def chat_json(messages, max_tokens=4000, temperature=0.4, think=True, timeout=90):
    """Ask for a JSON object. DeepSeek JSON mode can occasionally return empty content,
    so retry once before giving up."""
    for _ in range(2):
        data = _parse_json(_call(messages, max_tokens, True, temperature, think=think, timeout=timeout).content)
        if data is not None:
            return data
    raise LLMError("The model returned an unreadable answer. Please try again.")


def chat_json_with_tools(messages, tools, handlers, max_tokens=4000, temperature=0.7, max_rounds=3, timeout=90):
    """Like chat_json, but the model may call tools first. `handlers` maps tool name to a
    function taking the parsed arguments and returning a JSON-serializable result.
    Returns (data, calls) where calls lists {"name", "args", "result"} for each tool call."""
    messages = list(messages)
    calls = []
    empty_retries = 1
    for round_no in range(max_rounds + 1):
        offer_tools = tools if round_no < max_rounds else None  # last round: answer only
        msg = _call(messages, max_tokens, True, temperature, tools=offer_tools, timeout=timeout)
        if msg.tool_calls:
            messages.append(msg.model_dump(exclude_none=True))
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                handler = handlers.get(tc.function.name)
                result = handler(args) if handler else {"error": f"Unknown tool {tc.function.name}"}
                calls.append({"name": tc.function.name, "args": args, "result": result})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, ensure_ascii=False)})
            continue
        data = _parse_json(msg.content)
        if data is not None:
            return data, calls
        if empty_retries:
            empty_retries -= 1
            continue
        break
    raise LLMError("The model returned an unreadable answer. Please try again.")
