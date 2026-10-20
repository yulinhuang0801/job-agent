"""Cold emails and application-form help for one job or firm."""
import json
import re
from datetime import date, timedelta

import httpx
from bs4 import BeautifulSoup

from . import assess, llm, memory, prompts, store
from .jobs_fetch import HEADERS

DEFAULT_OUTREACH = {"intro": "", "hours_per_week": "", "start": "", "signature": "", "examples": ""}


def load_details():
    return {**DEFAULT_OUTREACH, **store.load("outreach", {})}


def _candidate(profile):
    keep = {k: v for k, v in profile.items() if k not in ("evaluation",)}
    stories = assess.stories_for_profile() or {}
    return json.dumps({"profile": keep, "stories": stories.get("stories", [])[:8]}, ensure_ascii=False)[:20000] \
        + "\n\n" + memory.shared_block(max_facts=30, max_events=0)


def _firm(job):
    return json.dumps({k: job.get(k) for k in ("company", "title", "website", "url", "research", "contact")}, ensure_ascii=False)[:12000]


def write_email(job):
    profile = store.load("profile", {})
    if not profile:
        raise llm.LLMError("Build your profile first.")
    d = load_details()
    out = llm.chat_json([
        {"role": "system", "content": prompts.EMAIL_SYSTEM.format(examples=d["examples"] or "(none given; write a concise, warm, specific email)")},
        {"role": "user", "content": "OUTREACH DETAILS:\n" + json.dumps({k: d[k] for k in ("intro", "hours_per_week", "start", "signature")}, ensure_ascii=False)
                                    + "\n\nCANDIDATE:\n" + _candidate(profile) + "\n\nFIRM / JOB:\n" + _firm(job)
                                    + (f"\n\nJOB POSTING:\n{job['description'][:5000]}" if job.get("description") else "")},
    ], max_tokens=8000, temperature=0.6, timeout=150)
    job["email"] = {k: str(out.get(k) or "") for k in ("subject", "body", "linkedin_note", "followup")}
    job["email"]["hooks_used"] = out.get("hooks_used") or []
    return job


def mark_sent(job):
    today = date.today()
    job["status"] = "Contacted"
    job["applied_date"] = job.get("applied_date") or today.isoformat()
    job["contacted_date"] = today.isoformat()
    job["followup_due"] = (today + timedelta(days=7)).isoformat()
    memory.log("job", f"Emailed {job.get('company')}" + (f" ({(job.get('contact') or {}).get('name')})" if (job.get("contact") or {}).get("name") else ""), job["id"])
    return job


# ---------- Application forms ----------

def _form_text_from_url(url):
    try:
        r = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=20)
        r.raise_for_status()
    except httpx.HTTPError:
        raise llm.LLMError("Couldn't open that page. Copy the form's questions from the page and paste them instead.")
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    lines = []
    for el in soup.find_all(["label", "legend", "h1", "h2", "h3", "select", "textarea", "input"]):
        if el.name == "select":
            opts = [o.get_text(strip=True) for o in el.find_all("option") if o.get_text(strip=True)]
            lines.append("  options: " + " | ".join(opts[:30]))
        elif el.name in ("input", "textarea"):
            kind = el.get("type", "textarea" if el.name == "textarea" else "text")
            if kind not in ("hidden", "submit", "button"):
                lines.append(f"  [{kind} field{' required' if el.has_attr('required') else ''}]")
        else:
            t = el.get_text(" ", strip=True)
            if t:
                lines.append(t)
    text = "\n".join(lines)
    if len(text) < 80:
        raise llm.LLMError("This page builds its form with JavaScript, so it can't be read automatically. "
                           "Copy the questions from the form and paste them instead.")
    return text[:20000]


def parse_form(job, text="", url=""):
    if not text and url:
        text = _form_text_from_url(url)
    if not text.strip():
        raise llm.LLMError("Paste the form's questions, or give the application link.")
    parsed = llm.chat_json([
        {"role": "system", "content": prompts.FORM_PARSE_SYSTEM},
        {"role": "user", "content": text[:20000]},
    ], max_tokens=8000, temperature=0.1, think=False, timeout=120)
    questions = [q for q in parsed.get("questions") or [] if q.get("question")]
    if not questions:
        raise llm.LLMError("Couldn't find any questions in that text.")
    profile = store.load("profile", {})
    d = load_details()
    drafts = llm.chat_json([
        {"role": "system", "content": prompts.FORM_ANSWER_SYSTEM},
        {"role": "user", "content": "QUESTIONS:\n" + json.dumps([{"index": i, **q} for i, q in enumerate(questions)], ensure_ascii=False)
                                    + "\n\nOUTREACH DETAILS:\n" + json.dumps(d, ensure_ascii=False)[:3000]
                                    + "\n\nCANDIDATE:\n" + _candidate(profile) + "\n\nFIRM / JOB:\n" + _firm(job)
                                    + (f"\n\nJOB POSTING:\n{job['description'][:5000]}" if job.get("description") else "")},
    ], max_tokens=16000, temperature=0.4, timeout=180)
    by_index = {int(a.get("index", -1)): a for a in drafts.get("answers") or [] if str(a.get("index", "")).lstrip("-").isdigit()}
    job["form"] = {
        "source": url or "pasted",
        "title": parsed.get("title") or "",
        "step": 0,
        "questions": [{**q, "options": q.get("options") or [], "answer": str((by_index.get(i) or {}).get("answer") or ""),
                       "note": str((by_index.get(i) or {}).get("note") or ""), "done": False} for i, q in enumerate(questions)],
    }
    job["channel"] = "form"
    return job


def rewrite_answer(job, index, instruction):
    q = job["form"]["questions"][index]
    out = llm.chat_json([
        {"role": "system", "content": prompts.FORM_ANSWER_SYSTEM},
        {"role": "user", "content": "QUESTIONS:\n" + json.dumps([{"index": 0, **{k: q[k] for k in ("question", "type", "options", "category")}}], ensure_ascii=False)
                                    + f"\n\nCURRENT DRAFT:\n{q['answer']}\n\nCANDIDATE'S REQUEST:\n{instruction or 'Make it stronger and more specific.'}"
                                    + "\n\nCANDIDATE:\n" + _candidate(store.load("profile", {})) + "\n\nFIRM / JOB:\n" + _firm(job)},
    ], max_tokens=4000, temperature=0.5)
    ans = (out.get("answers") or [{}])[0]
    q["answer"], q["note"] = str(ans.get("answer") or q["answer"]), str(ans.get("note") or "")
    return job


def form_txt(job):
    f = job.get("form") or {}
    lines = [f"Application: {job.get('title') or ''} at {job.get('company') or ''}".strip(),
             f"Link: {job.get('apply_url') or job.get('url') or f.get('source', '')}", ""]
    for i, q in enumerate(f.get("questions", []), 1):
        lines.append(f"{i}. {q['question']}" + (" (required)" if q.get("required") else ""))
        if q.get("options"):
            lines.append("   Options: " + " | ".join(map(str, q["options"])))
        lines.append("   Answer: " + (q["answer"] or "(fill in yourself)"))
        if q.get("note"):
            lines.append("   Note: " + q["note"])
        lines.append("")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
