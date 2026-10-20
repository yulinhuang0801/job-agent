"""Job Agent web app: intake interview → profile → per-job fit, resume, cover letter → tracker."""
import csv
import io
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import assess, llm, outreach, pdf, prompts, research, resume_text, services, sourcing, store
from .jobs_fetch import FetchError, fetch_job

STATIC = Path(__file__).resolve().parent.parent / "static"
STATUSES = ["Saved", "Ready", "Contacted", "Followed up", "Applied", "Interviewing", "Offer", "Rejected", "Withdrawn"]
MAX_JD_CHARS = 12000
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

app = FastAPI(title="Job Agent")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def _revalidate_page(request: Request, call_next):
    """Make browsers re-check the page and scripts so updates show up without a hard refresh."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(llm.LLMError)
def _llm_error(request: Request, exc: llm.LLMError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


# ---------- Settings ----------

SECRET_FIELDS = ("api_key", "backup_key", "search_key", "stt_key", "hunter_key", "serper_key")


class SettingsIn(BaseModel):
    api_key: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    search_key: Optional[str] = None
    stt_key: Optional[str] = None
    stt_base_url: Optional[str] = None
    stt_model: Optional[str] = None
    stt_provider: Optional[str] = None
    backup_key: Optional[str] = None
    backup_base_url: Optional[str] = None
    backup_model: Optional[str] = None
    hunter_key: Optional[str] = None
    serper_key: Optional[str] = None


def _hint(key):
    return f"{key[:4]}…{key[-4:]}" if len(key) > 10 else ""


def _public_settings():
    s = store.get_settings()
    return {
        "model": s["model"],
        "base_url": s["base_url"],
        "stt_base_url": s["stt_base_url"],
        "stt_model": s["stt_model"],
        "stt_provider": s["stt_provider"],
        "has_key": bool(s["api_key"]),
        "key_hint": _hint(s["api_key"]),
        "has_search": bool(s["search_key"]),
        "search_hint": _hint(s["search_key"]),
        "has_stt": bool(s["stt_key"]),
        "stt_hint": _hint(s["stt_key"]),
        "backup_base_url": s["backup_base_url"],
        "backup_model": s["backup_model"],
        "has_backup": bool(s["backup_key"]),
        "backup_hint": _hint(s["backup_key"]),
        "has_hunter": bool(s["hunter_key"]),
        "hunter_hint": _hint(s["hunter_key"]),
        "has_serper": bool(s["serper_key"]),
        "serper_hint": _hint(s["serper_key"]),
    }


@app.get("/api/settings")
def get_settings():
    return _public_settings()


@app.put("/api/settings")
def put_settings(body: SettingsIn):
    s = store.get_settings()
    for field, value in body.model_dump(exclude_none=True).items():
        value = value.strip()
        if not value:  # blank secret box means "keep the saved one"
            continue
        if field in SECRET_FIELDS and value.startswith("http"):
            # A whole example request was pasted (Hunter's dashboard shows these); keep only its key.
            value = (parse_qs(urlparse(value).query).get("api_key") or [value])[0]
        s[field] = value.rstrip("/") if field.endswith("base_url") else value
    store.save("settings", s)
    return _public_settings()


@app.delete("/api/settings/{field}")
def delete_secret(field: str):
    if field not in SECRET_FIELDS:
        raise HTTPException(404, "Unknown setting.")
    s = store.get_settings()
    s[field] = ""
    store.save("settings", s)
    return _public_settings()


@app.post("/api/settings/test")
def test_settings(which: str = "main"):
    if which == "hunter":
        try:
            left = services.hunter_check()
        except services.ServiceError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "provider": "Hunter.io", "model": f"{left} lookups left this month"}
    if which == "stt":
        try:
            services.transcribe("test.wav", services.silent_wav(), "audio/wav")
        except services.ServiceError as e:
            raise HTTPException(400, str(e))
        s = store.get_settings()
        return {"ok": True, "provider": "AssemblyAI" if s["stt_provider"] == "assemblyai" else "speech-to-text", "model": s["stt_model"]}
    name, model, reply = llm.test_provider(which)
    return {"ok": True, "provider": name, "model": model, "reply": reply}


# ---------- Intake interview ----------

EMPTY_INTERVIEW = {"messages": [], "section": "goals", "progress": 0, "complete": False,
                   "resume_name": "", "resume_text": "", "research": []}


class MessageIn(BaseModel):
    text: str


def _load_interview():
    return {**EMPTY_INTERVIEW, **store.load("interview", {})}


@app.get("/api/interview")
def get_interview():
    state = _load_interview()
    state.pop("resume_text")
    return {**state, "search_enabled": services.search_enabled()}


def _research_notes(research):
    lines = []
    for r in research[-12:]:
        res = r["result"]
        snippet = res.get("answer") or " ".join(x.get("title", "") + ": " + x.get("content", "")[:200] for x in res.get("results", [])[:2])
        lines.append(f"- {r['query']}: {snippet[:400]}")
    return "\n".join(lines)


def _interview_system(state, search_on):
    text = prompts.INTERVIEW_SYSTEM + (prompts.INTERVIEW_SEARCH_RULES if search_on else prompts.INTERVIEW_NO_SEARCH_RULES)
    if state["resume_text"]:
        text += prompts.INTERVIEW_RESUME_BLOCK.format(resume=state["resume_text"])
    if state["research"]:
        text += prompts.INTERVIEW_RESEARCH_BLOCK.format(research=_research_notes(state["research"]))
    return text


def _interview_turn(state, user_message):
    """Send one candidate message to the coach (who may search the web first) and save the reply."""
    messages = state["messages"] + [user_message]
    search_on = services.search_enabled()
    model_messages = [{"role": "system", "content": _interview_system(state, search_on)}] + [_for_model(m) for m in messages]
    if search_on:
        out, calls = llm.chat_json_with_tools(model_messages, [services.SEARCH_TOOL], {"web_search": services.web_search})
    else:
        out, calls = llm.chat_json(model_messages, max_tokens=4000, temperature=0.7), []
    reply = str(out.get("reply") or "").strip()
    if not reply:
        raise llm.LLMError("The interviewer returned an empty reply. Please send your message again.")
    lookups = [c["args"].get("query", "") for c in calls if c["name"] == "web_search"]
    section = out.get("section") or state["section"]
    progress = max(0, min(100, int(out.get("progress") or state["progress"])))
    complete = bool(out.get("complete"))
    state.update({
        "messages": messages + [{"role": "assistant", "content": reply, "section": section, "progress": progress,
                                 "complete": complete, "lookups": lookups}],
        "section": section,
        "progress": progress,
        "complete": complete,
        "research": state["research"] + [{"query": c["args"].get("query", ""), "result": c["result"]}
                                         for c in calls if c["name"] == "web_search" and "error" not in c["result"]],
    })
    store.save("interview", state)
    return get_interview()


def _for_model(m):
    """DeepSeek's JSON mode returns blank replies if earlier assistant turns in the history
    aren't JSON too, so replay them in the same shape the model is asked to produce."""
    if m["role"] != "assistant":
        return {"role": m["role"], "content": m["content"]}
    shaped = {"reply": m["content"], "section": m.get("section", "goals"),
              "progress": m.get("progress", 0), "complete": m.get("complete", False)}
    return {"role": "assistant", "content": json.dumps(shaped, ensure_ascii=False)}


@app.post("/api/interview/message")
def interview_message(body: MessageIn):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Message is empty.")
    return _interview_turn(_load_interview(), {"role": "user", "content": text})


@app.post("/api/interview/resume")
async def upload_resume(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "That file is too large (10 MB max).")
    try:
        text = resume_text.extract_text(file.filename, data)
    except resume_text.ResumeError as e:
        raise HTTPException(400, str(e))
    state = _load_interview()
    state["resume_name"], state["resume_text"] = file.filename, text
    note = (f"I uploaded my resume ({file.filename}). Tell me briefly what you picked up from it, "
            "then ask about what's missing.")
    return await run_in_threadpool(_interview_turn, state, {"role": "user", "content": note, "kind": "upload"})


@app.post("/api/interview/reset")
def interview_reset():
    store.save("interview", EMPTY_INTERVIEW)
    return get_interview()


@app.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES * 2:
        raise HTTPException(400, "Recording is too long. Try shorter answers.")
    try:
        text = await run_in_threadpool(services.transcribe, file.filename, data, file.content_type)
    except services.ServiceError as e:
        raise HTTPException(400, str(e))
    return {"text": text}


# ---------- Stories (behavioral) ----------

class StoryAdd(BaseModel):
    competency: str


@app.get("/api/stories")
def get_stories():
    return assess.public_stories(assess.load_stories())


@app.post("/api/stories/start")
def start_stories():
    return assess.start_stories()


@app.post("/api/stories/message")
def story_message(body: MessageIn):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Message is empty.")
    return assess.story_turn(text)


@app.post("/api/stories/import")
async def import_stories(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "That file is too large (10 MB max).")
    try:
        text = resume_text.extract_text(file.filename, data)
    except resume_text.ResumeError as e:
        raise HTTPException(400, str(e).replace("CV", "document"))
    return await run_in_threadpool(assess.import_material, text, file.filename)


@app.post("/api/stories/add")
def add_story(body: StoryAdd):
    return assess.add_story(body.competency)


@app.post("/api/stories/reset")
def reset_stories():
    return assess.reset_stories()


# ---------- Skills check (technical) ----------

class AnswersIn(BaseModel):
    answers: dict


@app.get("/api/skills")
def get_skills():
    return assess.public_skills(assess.load_skills())


@app.post("/api/skills/start")
def start_skills():
    return assess.start_skills()


@app.put("/api/skills/answers")
def save_skill_answers(body: AnswersIn):
    return assess.public_skills(assess.save_answers(body.answers))


@app.post("/api/skills/grade")
def grade_skills(body: AnswersIn):
    return assess.grade_skills(body.answers)


@app.post("/api/skills/reset")
def reset_skills():
    return assess.reset_skills()


# ---------- Profile ----------

@app.get("/api/profile")
def get_profile():
    return store.load("profile", {})


@app.put("/api/profile")
def put_profile(profile: dict):
    store.save("profile", profile)
    return profile


@app.post("/api/profile/build")
def build_profile():
    state = _load_interview()
    if len(state["messages"]) < 4 and not state["resume_text"] and not assess.stories_for_profile():
        raise HTTPException(400, "Upload your CV or answer a few interview questions first.")
    transcript = "\n\n".join(
        f"{'Candidate' if m['role'] == 'user' else 'Coach'}: {m['content']}" for m in state["messages"]
    )
    parts = []
    if state["resume_text"]:
        parts.append(f"UPLOADED RESUME:\n{state['resume_text']}")
    parts.append(f"INTERVIEW TRANSCRIPT:\n{transcript or '(none yet)'}")
    if state["research"]:
        parts.append(f"BACKGROUND THE COACH LOOKED UP:\n{_research_notes(state['research'])}")
    stories = assess.stories_for_profile()
    if stories:
        parts.append("STORIES (behavioral STAR stories and prepared answers):\n" + json.dumps(stories, ensure_ascii=False))
    skills = assess.skills_for_profile()
    if skills:
        parts.append("SKILLS CHECK RESULTS:\n" + json.dumps(skills, ensure_ascii=False))
    profile = llm.chat_json([
        {"role": "system", "content": prompts.PROFILE_SYSTEM},
        {"role": "user", "content": "\n\n".join(parts)},
    ], max_tokens=32000, temperature=0.2, think=False, timeout=240)
    store.save("profile", profile)
    return profile


def _require_profile():
    profile = store.load("profile", {})
    if not profile:
        raise HTTPException(400, "Build your profile first (Interview tab → Build my profile).")
    return profile


def _header(profile):
    return {k: profile.get(k, "") for k in ("name", "email", "phone", "location", "links")}


def _resume_from_profile(p):
    sk = p.get("skills") or {}
    groups = []
    tech = [s.get("name") if isinstance(s, dict) else s for s in sk.get("technical") or []]
    if tech:
        groups.append({"label": "Technical", "items": [t for t in tech if t]})
    if sk.get("tools"):
        groups.append({"label": "Tools", "items": sk["tools"]})
    if sk.get("spoken_languages"):
        groups.append({"label": "Languages", "items": sk["spoken_languages"]})
    return {
        "summary": p.get("summary", ""),
        "experience": p.get("experience") or [],
        "projects": p.get("projects") or [],
        "education": p.get("education") or [],
        "skills": groups,
    }


def _slug(*parts):
    s = "_".join(p for p in parts if p)
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    return s[:60] or "document"


def _pdf_response(data, filename):
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'})


@app.get("/api/profile/resume.pdf")
def master_resume_pdf():
    profile = _require_profile()
    data = pdf.render_resume(_header(profile), _resume_from_profile(profile))
    return _pdf_response(data, _slug(profile.get("name"), "Resume"))


# ---------- Jobs + tracker ----------

class JobIn(BaseModel):
    url: str = ""
    description: str = ""
    title: str = ""
    company: str = ""


class JobPatch(BaseModel):
    title: Optional[str] = None
    company: Optional[str] = None
    status: Optional[str] = None
    applied_date: Optional[str] = None
    notes: Optional[str] = None
    url: Optional[str] = None
    website: Optional[str] = None
    channel: Optional[str] = None
    contact_name: Optional[str] = None
    contact_title: Optional[str] = None
    contact_email: Optional[str] = None


def _jobs():
    return store.load("jobs", [])


def _find(jobs, job_id):
    for job in jobs:
        if job["id"] == job_id:
            return job
    raise HTTPException(404, "Job not found.")


@app.get("/api/jobs")
def list_jobs():
    return _jobs()


@app.post("/api/jobs")
def add_job(body: JobIn):
    url, description = body.url.strip(), body.description.strip()
    title, company = body.title.strip(), body.company.strip()
    if not url and not description:
        raise HTTPException(400, "Give a job link or paste the job description.")
    if url and not description:
        try:
            fetched = fetch_job(url)
        except FetchError as e:
            raise HTTPException(400, str(e))
        description = fetched["description"]
        title = title or fetched["title"]
        company = company or fetched["company"]
    job = {
        "id": uuid.uuid4().hex[:10],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "url": url, "title": title, "company": company, "location": "",
        "description": description[:MAX_JD_CHARS],
        "status": "Saved", "applied_date": "", "notes": "",
        "fit": None, "documents": None,
    }
    jobs = _jobs()
    jobs.insert(0, job)
    store.save("jobs", jobs)
    return job


@app.patch("/api/jobs/{job_id}")
def update_job(job_id: str, body: JobPatch):
    jobs = _jobs()
    job = _find(jobs, job_id)
    changes = body.model_dump(exclude_none=True)
    if "status" in changes and changes["status"] not in STATUSES:
        raise HTTPException(400, f"Status must be one of: {', '.join(STATUSES)}")
    if changes.get("status") == "Applied" and not job.get("applied_date") and "applied_date" not in changes:
        changes["applied_date"] = datetime.now().date().isoformat()
    contact = {k[8:]: changes.pop(k) for k in list(changes) if k.startswith("contact_")}
    if contact:
        job["contact"] = {**(job.get("contact") or {}), **contact}
        if "email" in contact:
            job["contact"]["email_source"] = "entered by you"
    job.update(changes)
    store.save("jobs", jobs)
    return job


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    jobs = _jobs()
    _find(jobs, job_id)
    store.save("jobs", [j for j in jobs if j["id"] != job_id])
    return {"ok": True}


def _profile_for_ai(profile):
    return json.dumps({k: v for k, v in profile.items() if k != "evaluation"}, ensure_ascii=False)


def _posting(job):
    """What the AI knows about this opportunity: the posting text, and for firms the website research."""
    head = f"Title: {job['title']}\nCompany: {job['company']}\nURL: {job['url']}\n\n" if job["title"] or job["company"] else ""
    extra = ""
    if job.get("research"):
        extra += "\n\nFIRM RESEARCH:\n" + json.dumps(job["research"], ensure_ascii=False)[:6000]
    if (job.get("contact") or {}).get("name"):
        c = job["contact"]
        extra += f"\n\nADDRESS THE COVER LETTER TO: {c['name']}, {c.get('title', '')}"
    if not job.get("description"):
        head += "(No job posting: this is a firm the candidate is reaching out to about a part-time or remote internship.)\n"
    return head + (job.get("description") or "") + extra


@app.post("/api/jobs/{job_id}/analyze")
def analyze_job(job_id: str):
    profile = _require_profile()
    jobs = _jobs()
    job = _find(jobs, job_id)
    fit = llm.chat_json([
        {"role": "system", "content": prompts.FIT_SYSTEM},
        {"role": "user", "content": f"CANDIDATE PROFILE (json):\n{_profile_for_ai(profile)}\n\nJOB POSTING:\n{_posting(job)}"},
    ], max_tokens=8000, temperature=0.2)
    job["fit"] = fit
    for field in ("title", "company", "location"):
        if not job.get(field) and fit.get(field):
            job[field] = fit[field]
    store.save("jobs", jobs)
    return job


@app.post("/api/jobs/{job_id}/documents")
def generate_documents(job_id: str):
    profile = _require_profile()
    jobs = _jobs()
    job = _find(jobs, job_id)
    docs = llm.chat_json([
        {"role": "system", "content": prompts.TAILOR_SYSTEM},
        {"role": "user", "content": f"CANDIDATE PROFILE (json):\n{_profile_for_ai(profile)}\n\nJOB POSTING:\n{_posting(job)}"},
    ], max_tokens=16000, temperature=0.4, timeout=180)
    job["documents"] = docs
    if job["status"] == "Saved":
        job["status"] = "Ready"
    store.save("jobs", jobs)
    return job


def _job_docs(job_id):
    profile = _require_profile()
    job = _find(_jobs(), job_id)
    if not job.get("documents"):
        raise HTTPException(400, "Generate the documents for this job first.")
    return profile, job


@app.get("/api/jobs/{job_id}/resume.pdf")
def job_resume_pdf(job_id: str):
    profile, job = _job_docs(job_id)
    data = pdf.render_resume(_header(profile), job["documents"])
    return _pdf_response(data, _slug(profile.get("name"), job.get("company"), "Resume"))


@app.get("/api/jobs/{job_id}/cover-letter.pdf")
def job_cover_letter_pdf(job_id: str):
    profile, job = _job_docs(job_id)
    data = pdf.render_cover_letter(_header(profile), job["documents"].get("cover_letter") or {}, job.get("company", ""))
    return _pdf_response(data, _slug(profile.get("name"), job.get("company"), "Cover_Letter"))


@app.get("/api/jobs.csv")
def export_csv():
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Company", "Role", "Category", "Location", "Status", "Channel", "Contact", "Contact title", "Contact email",
                     "Fit score", "Verdict", "Applied/contacted", "Follow up by", "Notes", "Website", "Link", "Added"])
    for j in _jobs():
        fit, c = j.get("fit") or {}, j.get("contact") or {}
        writer.writerow([j["company"], j["title"], j.get("category", ""), j.get("location", ""), j["status"], j.get("channel", ""),
                         c.get("name", ""), c.get("title", ""), c.get("email", ""), fit.get("score", ""), fit.get("verdict", ""),
                         j["applied_date"], j.get("followup_due", ""), j["notes"], j.get("website", ""), j["url"], j["created_at"][:10]])
    # BOM so Excel opens Chinese text correctly
    return Response("﻿" + buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="applications.csv"'})


# ---------- Find (sourcing) ----------

class SearchIn(BaseModel):
    category: str
    location: str = ""
    industry: str = ""
    role: str = ""


class ImportIn(BaseModel):
    text: str


class AddIn(BaseModel):
    candidates: list


class SourcesIn(BaseModel):
    sites: list = []
    queries: list = []


@app.get("/api/find/categories")
def find_categories():
    return {"categories": sourcing.categories(), "search_enabled": bool(services.engine_name()),
            "engine": services.engine_name(), "sources": sourcing.load_sources()}


@app.put("/api/find/sources")
def find_sources(body: SourcesIn):
    return sourcing.save_sources([str(x) for x in body.sites], [str(x) for x in body.queries])


@app.post("/api/find/search")
def find_search(body: SearchIn):
    return sourcing.search(body.category, body.location.strip(), body.industry.strip(), body.role.strip())


@app.post("/api/find/import")
def find_import(body: ImportIn):
    rows = sourcing.parse_import(body.text)
    if not rows:
        raise HTTPException(400, "Couldn't find any firm names or websites in that text.")
    return {"candidates": rows}


@app.post("/api/find/add")
def find_add(body: AddIn):
    jobs = _jobs()
    have = {sourcing._domain(j.get("website") or j.get("url") or "") for j in jobs} - {""}
    added = []
    for c in body.candidates:
        dom = sourcing._domain(c.get("website") or c.get("posting_url") or "")
        if dom and dom in have and c.get("kind") != "posting":
            continue
        posting = c.get("kind") == "posting"
        job = {
            "id": uuid.uuid4().hex[:10], "created_at": datetime.now().isoformat(timespec="seconds"),
            "kind": "posting" if posting else "firm", "category": c.get("category", ""),
            "url": c.get("posting_url") if posting else (c.get("website") or ""), "website": c.get("website") or "",
            "title": c.get("title") or ("" if posting else "Internship (reach out)"), "company": c.get("name", ""),
            "location": c.get("location", ""), "description": "", "status": "Saved", "applied_date": "",
            "notes": (c.get("reasons") or "") + (f" ⚠ {c['red_flags']}" if c.get("red_flags") else ""),
            "fit": None, "documents": None, "channel": "form" if posting else "",
            "contact": {k: v for k, v in {"name": c.get("contact_name", ""), "title": c.get("contact_title", ""),
                                          "email": c.get("contact_email", ""),
                                          "email_source": "from your imported list" if c.get("contact_email") else ""}.items()},
        }
        if posting and job["url"]:
            try:
                job["description"] = fetch_job(job["url"])["description"][:MAX_JD_CHARS]
            except FetchError:
                pass
        jobs.insert(0, job)
        have.add(dom)
        added.append(job)
    store.save("jobs", jobs)
    return {"added": len(added), "jobs": jobs}


# ---------- Research, cold email, application forms ----------

def _update_job(job_id, fn, *args):
    jobs = _jobs()
    job = _find(jobs, job_id)
    fn(job, *args)
    store.save("jobs", jobs)
    return job


class FormIn(BaseModel):
    text: str = ""
    url: str = ""


class FormAnswerIn(BaseModel):
    index: int
    answer: Optional[str] = None
    done: Optional[bool] = None
    instruction: Optional[str] = None


@app.post("/api/jobs/{job_id}/research")
def research_job(job_id: str):
    return _update_job(job_id, research.research)


class PatternIn(BaseModel):
    known_email: str
    known_name: str


@app.post("/api/jobs/{job_id}/email-pattern")
def email_pattern(job_id: str, body: PatternIn):
    return _update_job(job_id, research.email_from_example, body.known_email, body.known_name)


@app.post("/api/jobs/{job_id}/email")
def email_job(job_id: str):
    return _update_job(job_id, outreach.write_email)


class EmailEdit(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None
    linkedin_note: Optional[str] = None
    followup: Optional[str] = None


@app.patch("/api/jobs/{job_id}/email")
def edit_email(job_id: str, body: EmailEdit):
    def apply(job):
        job["email"] = {**(job.get("email") or {}), **body.model_dump(exclude_none=True)}
    return _update_job(job_id, apply)


@app.post("/api/jobs/{job_id}/sent")
def email_sent(job_id: str):
    return _update_job(job_id, outreach.mark_sent)


@app.post("/api/jobs/{job_id}/form")
def form_parse(job_id: str, body: FormIn):
    return _update_job(job_id, outreach.parse_form, body.text, body.url.strip())


@app.patch("/api/jobs/{job_id}/form")
def form_answer(job_id: str, body: FormAnswerIn):
    def apply(job):
        qs = (job.get("form") or {}).get("questions") or []
        if not 0 <= body.index < len(qs):
            raise HTTPException(404, "No such question.")
        if body.answer is not None:
            qs[body.index]["answer"] = body.answer
        if body.done is not None:
            qs[body.index]["done"] = body.done
        job["form"]["step"] = body.index
    return _update_job(job_id, apply)


@app.post("/api/jobs/{job_id}/form/rewrite")
def form_rewrite(job_id: str, body: FormAnswerIn):
    def apply(job):
        if not 0 <= body.index < len((job.get("form") or {}).get("questions") or []):
            raise HTTPException(404, "No such question.")
        outreach.rewrite_answer(job, body.index, body.instruction or "")
    return _update_job(job_id, apply)


@app.get("/api/jobs/{job_id}/form.txt")
def form_txt(job_id: str):
    job = _find(_jobs(), job_id)
    if not job.get("form"):
        raise HTTPException(400, "Load the application form first.")
    name = _slug(job.get("company"), "Application")
    return Response("\ufeff" + outreach.form_txt(job), media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}.txt"'})


@app.get("/api/outreach")
def get_outreach():
    return outreach.load_details()


@app.put("/api/outreach")
def put_outreach(details: dict):
    clean = {k: str(details.get(k, "")) for k in outreach.DEFAULT_OUTREACH}
    store.save("outreach", clean)
    return clean


@app.get("/api/meta")
def meta():
    return {"statuses": STATUSES}
