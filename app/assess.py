"""The Stories step (behavioral questions with STAR follow-ups) and the Skills check
(a short technical quiz picked from the question bank and graded against rubrics)."""
import json
import uuid
from pathlib import Path

from . import llm, memory, prompts, store

BANK = json.loads((Path(__file__).resolve().parent / "question_bank.json").read_text(encoding="utf-8"))
BEHAVIORAL = BANK["behavioral"]
TECHNICAL = BANK["technical"]["areas"]
MAX_FOLLOWUPS = 3


def candidate_background(max_chars=5000):
    """A compact picture of the candidate for the AI: the profile if built, else interview answers."""
    profile = store.load("profile", {})
    shared = memory.shared_block(max_facts=30, max_events=0)
    if profile:
        keep = {k: profile.get(k) for k in ("targets", "summary", "education", "experience", "projects", "skills")}
        return json.dumps(keep, ensure_ascii=False)[:max_chars] + ("\n\n" + shared if shared else "")
    interview = store.load("interview", {})
    said = [m["content"] for m in interview.get("messages", []) if m["role"] == "user" and m.get("kind") != "upload"]
    text = (interview.get("resume_text") or "")[:2500] + "\n\nInterview answers:\n" + "\n".join(said)
    return text[-max_chars:] + ("\n\n" + shared if shared else "")


# ---------- Your own questions (data/custom_questions.json) ----------
# Users (and Company prep) can add questions next to the built-in bank. Behavioral ones become extra
# question types on the Stories tab; technical ones become their own area in the Skills check.

def load_custom():
    return {"technical": [], "behavioral": [], **store.load("custom_questions", {})}


def comps():
    merged = dict(BEHAVIORAL["competencies"])
    for q in load_custom()["behavioral"]:
        merged[q["id"]] = {"label": q["label"], "group": q.get("group") or "My questions", "kind": q.get("kind") or "story",
                           "questions": [q["question"]], "look_for": q.get("look_for") or "A specific, real example with what you did and the result."}
    return merged


def tech_areas():
    areas = {k: {"label": v["label"], "questions": list(v["questions"])} for k, v in TECHNICAL.items()}
    for q in load_custom()["technical"]:
        areas.setdefault("my:" + q["area"], {"label": q["area"], "questions": []})["questions"].append(q)
    return areas


def add_custom(kind, item):
    data = load_custom()
    item = {k: v for k, v in item.items() if v not in (None, "")}
    item["id"] = "my_" + uuid.uuid4().hex[:8]
    if kind == "behavioral":
        if not item.get("question"):
            raise llm.LLMError("Write the question first.")
        item.setdefault("label", item["question"][:60])
    else:
        if not (item.get("question") and item.get("area")):
            raise llm.LLMError("A technical question needs an area and the question.")
        item["level"] = int(item.get("level") or 2)
        item.setdefault("type", "concept")
        item.setdefault("rubric", "Judge correctness, completeness and clarity of the explanation.")
    data[kind].append(item)
    store.save("custom_questions", data)
    return data


def delete_custom(qid):
    data = load_custom()
    for kind in ("technical", "behavioral"):
        data[kind] = [q for q in data[kind] if q["id"] != qid]
    store.save("custom_questions", data)
    return data


# ---------- Stories ----------
# Each question type in the bank is either a STAR "story" or a short "answer". The candidate can
# chat through them one by one, or paste/upload a prep document that gets filed into all of them
# at once; the coach then only asks about gaps. Every turn sees the pasted material and every
# answer captured so far, not just the current question.

STAR_FIELDS = ("title", "situation", "task", "action", "result", "reflection")
ANSWER_FIELDS = ("answer",)
ALL_FIELDS = STAR_FIELDS + ANSWER_FIELDS
IMPORT_MIN_CHARS = 2500   # a message this long is treated as a pasted document
MATERIAL_MAX_CHARS = 60000


def _fields(comp):
    return STAR_FIELDS if comps()[comp]["kind"] == "story" else ANSWER_FIELDS


def _new_entry(comp):
    return {"competency": comp, "question": comps()[comp]["questions"][0], "followups": 0, "status": "pending",
            **{f: "" for f in ALL_FIELDS}, "missing": [], "quality": 0, "notes": ""}


def _empty_stories():
    return {"plan": list(BEHAVIORAL["core"]), "messages": [], "stories": {}, "sources": [], "done": False}


def load_stories():
    state = {**_empty_stories(), **store.load("stories", {})}
    state["plan"] = [c for c in state["plan"] if c in comps()]
    for comp in state["plan"]:
        entry = {**_new_entry(comp), **state["stories"].get(comp, {})}
        if entry["status"] == "active" and not any(m.get("competency") == comp for m in state["messages"]):
            entry["status"] = "pending"
        state["stories"][comp] = entry
    # Older saves: a long pasted message that was never filed as material.
    if not state["sources"]:
        for m in state["messages"]:
            if m["role"] == "user" and len(m["content"]) >= IMPORT_MIN_CHARS:
                state["sources"].append({"name": "Pasted document", "text": m["content"]})
                m["kind"] = "import"
    return state


def _current(state):
    for comp in state["plan"]:
        if state["stories"][comp]["status"] in ("pending", "active"):
            return comp
    return None


def public_stories(state):
    current = _current(state)
    return {
        "plan": state["plan"],
        "current": current,
        "index": state["plan"].index(current) if current else len(state["plan"]),
        "done": current is None and bool(state["messages"]),
        "messages": [{**m, "content": f"📄 Pasted my prep document ({len(m['content']):,} characters)"}
                     if m.get("kind") == "import" else m for m in state["messages"]],
        "stories": state["stories"],
        "sources": [{"name": s["name"], "chars": len(s["text"])} for s in state["sources"]],
        "labels": {c: comps()[c]["label"] for c in comps()},
        "kinds": {c: comps()[c]["kind"] for c in comps()},
        "groups": {c: comps()[c]["group"] for c in comps()},
        "extra_left": [c for c in BEHAVIORAL["extra"] + [q["id"] for q in load_custom()["behavioral"]] if c not in state["plan"]],
    }


def _has_content(entry):
    return any(entry.get(f) for f in ALL_FIELDS if f != "title")


def _ask_for(comp, entry):
    """What to ask when this question comes up: a gap-filling question if part of it is
    already captured, otherwise the bank question."""
    label = comps()[comp]["label"]
    if _has_content(entry) and entry["missing"]:
        about = f' ("{entry["title"]}")' if entry.get("title") else ""
        return f"{label}{about}: I have most of this already. What's still missing or vague: {', '.join(entry['missing'])}. Can you fill that in?"
    if _has_content(entry):
        return f"{label}: I have this from your notes. Anything you'd like to add or change? If not, say so and we'll move on."
    return f"{label}: {entry['question']}"


def _activate_next(state):
    """Mark the next unanswered question active and return the text that asks it."""
    nxt = _current(state)
    if not nxt:
        return None
    entry = state["stories"][nxt]
    entry["status"] = "active"
    return _ask_for(nxt, entry)


def start_stories():
    state = load_stories()
    if state["messages"]:
        store.save("stories", state)
        return public_stories(state)
    for comp in state["plan"]:
        state["stories"].setdefault(comp, _new_entry(comp))
    first = _activate_next(state)
    intro = ("Now let's prepare your behavioral answers: real stories (teamwork, conflict, failure...) plus a few "
             f"short answers like \"Tell me about yourself\". There are {len(state['plan'])} to start with. "
             "If you already have a prep document, paste it here or use 📄 Import prep doc, and I'll file "
             "everything and only ask about the gaps. Otherwise, let's go one by one.\n\n" + first)
    state["messages"].append({"role": "assistant", "content": intro, "competency": _current(state)})
    store.save("stories", state)
    return public_stories(state)


def _material(state):
    text = "\n\n---\n\n".join(f"[{s['name']}]\n{s['text']}" for s in state["sources"])
    return text[:MATERIAL_MAX_CHARS] or "(none)"


def _others_summary(state, skip):
    lines = []
    for comp, e in state["stories"].items():
        if comp == skip or not _has_content(e):
            continue
        gist = e.get("title") or (e.get("answer") or e.get("action") or "")[:120]
        lines.append(f"- {comps()[comp]['label']}: {gist} [{e['status']}]")
    return "\n".join(lines) or "(none yet)"


def import_material(text, name="Pasted document", record_message=True):
    """File a prep document into every matching question type, then ask about the first gap."""
    state = load_stories()
    if not any(s["text"] == text for s in state["sources"]):
        state["sources"].append({"name": name, "text": text})
    types = "\n".join(f"{cid} | {c['kind']} | {c['label']} | {c['look_for']}" for cid, c in comps().items())
    out = llm.chat_json([
        {"role": "system", "content": prompts.STORY_IMPORT_SYSTEM.format(types=types)},
        {"role": "user", "content": text[:MATERIAL_MAX_CHARS]},
    ], max_tokens=32000, temperature=0.2, timeout=240)

    filed, thin = [], []
    for item in out.get("items") or []:
        comp = item.get("type")
        if comp not in comps():
            continue
        if comp not in state["plan"]:
            state["plan"].append(comp)
        entry = state["stories"].setdefault(comp, _new_entry(comp))
        if entry["status"] == "done" and _has_content(entry):
            continue  # keep what was already worked through in chat
        for f in _fields(comp):
            if item.get(f):
                entry[f] = str(item[f])
        entry["missing"] = [str(m) for m in item.get("missing") or []]
        entry["quality"] = int(item.get("quality") or 0)
        entry["notes"] = str(item.get("notes") or "")
        complete = _has_content(entry) and not entry["missing"] and entry["quality"] >= 4
        entry["status"] = "done" if complete else "pending"
        (filed if complete else thin).append(comps()[comp]["label"])

    # Finished answers first, then partly filled ones (asked next), then ones not covered at all.
    def order(c):
        e = state["stories"].setdefault(c, _new_entry(c))
        return 0 if e["status"] == "done" else 1 if _has_content(e) else 2
    state["plan"].sort(key=order)
    for e in state["stories"].values():
        if e["status"] == "active":
            e["status"] = "pending"
    empty = [comps()[c]["label"] for c in state["plan"] if not _has_content(state["stories"][c])]
    nxt = _activate_next(state)

    parts = [f"I read your document and filed {len(filed) + len(thin)} answers."]
    if filed:
        parts.append("✓ Ready: " + ", ".join(filed) + ".")
    if thin:
        parts.append("Need a bit more: " + ", ".join(thin) + ".")
    if empty:
        parts.append("Not in the document yet: " + ", ".join(empty) + ".")
    if out.get("unmatched"):
        parts.append("Also noted, but didn't fit a question: " + "; ".join(map(str, out["unmatched"]))[:300] + ".")
    parts.append("You can review everything under \"Your answers\" below.")
    parts.append(("Let's fill the gaps. " + nxt) if nxt else "Everything is covered. Add more questions below, or go to the Skills check.")
    if record_message:
        state["messages"].append({"role": "user", "content": text, "kind": "import", "competency": None})
    state["messages"].append({"role": "assistant", "content": "\n\n".join(parts), "competency": _current(state)})
    store.save("stories", state)
    return public_stories(state)


def _story_for_model(m):
    if m["role"] != "assistant":
        content = "[I pasted my prep document; it's in the material above.]" if m.get("kind") == "import" else m["content"]
        return {"role": "user", "content": content}
    # DeepSeek's JSON mode needs earlier assistant turns in JSON shape too.
    return {"role": "assistant", "content": json.dumps({"reply": m["content"]}, ensure_ascii=False)}


def story_turn(text):
    if len(text) >= IMPORT_MIN_CHARS:
        return import_material(text)
    state = load_stories()
    if not state["messages"]:
        start_stories()
        state = load_stories()
    comp = _current(state)
    if not comp:
        raise llm.LLMError("All questions are done. Add another one below, or start over.")
    info, entry = comps()[comp], state["stories"][comp]
    entry["status"] = "active"
    fields = _fields(comp)

    following = [c for c in state["plan"] if c != comp and state["stories"][c]["status"] in ("pending", "active")]
    if following:
        nxt = following[0]
        next_text = _ask_for(nxt, state["stories"][nxt])
        next_block = f'When you move on, the next item is: "{next_text}"'
        next_instruction = "ask the next item in the candidate's language, keeping its meaning."
    else:
        next_block = "This is the last item."
        next_instruction = 'thank them and say the next step is the "Skills check" tab.'

    system = prompts.STORY_SYSTEM.format(
        label=info["label"], kind=info["kind"], question=entry["question"], look_for=info["look_for"],
        fields=", ".join(fields), field_shape=", ".join(f'"{f}": ""' for f in fields),
        current=json.dumps({f: entry[f] for f in fields}, ensure_ascii=False),
        followups=entry["followups"], max_followups=MAX_FOLLOWUPS, next_block=next_block,
        next_instruction=next_instruction, others=_others_summary(state, comp),
        material=_material(state), background=candidate_background(2500),
    )
    history = state["messages"][-16:] + [{"role": "user", "content": text}]
    out = llm.chat_json([{"role": "system", "content": system}] + [_story_for_model(m) for m in history],
                        max_tokens=4000, temperature=0.6)
    reply = str(out.get("reply") or "").strip()
    if not reply:
        raise llm.LLMError("The interviewer returned an empty reply. Please send your message again.")

    got = out.get("fields") or out.get("story") or {}
    for f in fields:
        if got.get(f):
            entry[f] = str(got[f])
    entry["missing"] = [str(m) for m in out.get("missing") or []]
    entry["quality"] = int(out.get("quality") or entry["quality"] or 0)
    state["messages"].append({"role": "user", "content": text, "competency": comp})
    if out.get("move_on") or out.get("skipped"):
        entry["status"] = "skipped" if out.get("skipped") else "done"
        if entry["status"] == "done":
            memory.log("story", f"Finished the \"{info['label']}\" answer on the Stories tab")
        _activate_next(state)
    else:
        entry["followups"] += 1
    state["messages"].append({"role": "assistant", "content": reply, "competency": _current(state) or comp})
    store.save("stories", state)
    return public_stories(state)


def add_story(comp):
    state = load_stories()
    if comp not in comps() or comp in state["plan"]:
        raise llm.LLMError("Pick a question you haven't added yet.")
    was_done = _current(state) is None
    state["plan"].append(comp)
    state["stories"][comp] = _new_entry(comp)
    if was_done:
        ask = _activate_next(state)
        state["messages"].append({"role": "assistant", "content": "One more. " + ask, "competency": comp})
    store.save("stories", state)
    return public_stories(state)


def reset_stories():
    store.save("stories", _empty_stories())
    return start_stories()


def stories_for_profile():
    state = load_stories()
    stories, answers = [], []
    for comp in state["plan"]:
        e = state["stories"][comp]
        if e["status"] == "skipped" or not _has_content(e):
            continue
        if comps()[comp]["kind"] == "story":
            stories.append({"theme": comps()[comp]["label"], **{f: e[f] for f in STAR_FIELDS}, "quality": e["quality"]})
        else:
            answers.append({"topic": comps()[comp]["label"], "answer": e["answer"]})
    return {"stories": stories, "prepared_answers": answers} if stories or answers else None


# ---------- Skills check ----------

def _empty_skills():
    return {"status": "none", "reason": "", "questions": [], "answers": {}, "results": None}


def load_skills():
    return {**_empty_skills(), **store.load("skills", {})}


def public_skills(state):
    """Hide rubrics until graded so the candidate can't read the answer key."""
    hide = state["status"] != "graded"
    qs = [{k: v for k, v in q.items() if not (hide and k == "rubric")} for q in state["questions"]]
    return {**state, "questions": qs}


def _bank_lines(only=None):
    lines = []
    for area_id, area in tech_areas().items():
        if only and area["label"] not in only and area_id not in only:
            continue
        for q in area["questions"]:
            short = q["question"].replace("\n", " ")[:150]
            lines.append(f"{q['id']} | {area['label']} | {q['level']} | {q['type']} | {short}")
    return "\n".join(lines)


def _bank_index():
    return {q["id"]: {**q, "area": area["label"]} for area in tech_areas().values() for q in area["questions"]}


def skill_areas():
    return [{"id": k, "label": v["label"], "count": len(v["questions"]), "mine": k.startswith("my:")} for k, v in tech_areas().items()]


def start_skills(count=5, areas=None):
    count = max(3, min(10, int(count or 5)))
    areas = [a for a in (areas or []) if a]
    bank = _bank_lines(areas or None)
    if not bank:
        raise llm.LLMError("No questions in those areas yet.")
    ask = f"\n\nThe candidate chose these areas, so use only them: {', '.join(areas)}." if areas else ""
    index = _bank_index()
    out = llm.chat_json([
        {"role": "system", "content": prompts.SKILLS_PICK_SYSTEM.format(count=count, bank=bank) + ask},
        {"role": "user", "content": f"CANDIDATE:\n{candidate_background()}"},
    ], max_tokens=6000, temperature=0.3)
    questions = [dict(index[i]) for i in out.get("picked") or [] if i in index]
    if not areas:
        for n, c in enumerate(out.get("custom") or []):
            if c.get("question") and c.get("rubric"):
                questions.append({"id": f"custom{n + 1}", "area": c.get("area") or "Other", "level": int(c.get("level") or 2),
                                  "type": c.get("type") or "concept", "question": c["question"], "rubric": c["rubric"],
                                  "custom": True})
    seen, unique = set(), []
    for q in questions:
        if q["id"] not in seen:
            seen.add(q["id"])
            unique.append(q)
    available = len(bank.splitlines())
    if len(unique) < min(3, count, available):
        raise llm.LLMError("Couldn't pick questions. Please try again.")
    state = {**_empty_skills(), "status": "answering", "reason": out.get("reason") or "", "questions": unique[:count]}
    store.save("skills", state)
    return public_skills(state)


def save_answers(answers):
    state = load_skills()
    if state["status"] == "graded":
        raise llm.LLMError("This skills check is already graded. Start a new one to answer again.")
    state["answers"] = {q["id"]: str(answers.get(q["id"], "")) for q in state["questions"]}
    store.save("skills", state)
    return state


def grade_skills(answers):
    state = save_answers(answers)
    items = [{"id": q["id"], "area": q["area"], "question": q["question"], "rubric": q["rubric"],
              "answer": state["answers"].get(q["id"], "").strip() or "(no answer)"} for q in state["questions"]]
    out = llm.chat_json([
        {"role": "system", "content": prompts.SKILLS_GRADE_SYSTEM},
        {"role": "user", "content": json.dumps(items, ensure_ascii=False)},
    ], max_tokens=12000, temperature=0.2, timeout=180)
    grades = {g.get("id"): g for g in out.get("grades") or []}
    results = {
        "grades": [{"id": q["id"], "score": max(0, min(4, int((grades.get(q["id"]) or {}).get("score") or 0))),
                    "feedback": (grades.get(q["id"]) or {}).get("feedback", ""),
                    "model_answer": (grades.get(q["id"]) or {}).get("model_answer", "")} for q in state["questions"]],
        "skills": out.get("skills") or [],
        "summary": out.get("summary") or "",
    }
    results["total"] = sum(g["score"] for g in results["grades"])
    results["max"] = 4 * len(results["grades"])
    state.update({"status": "graded", "results": results})
    memory.log("skills", f"Skills check graded: {results['total']}/{results['max']} ("
               + ", ".join(f"{s.get('area')}: {s.get('level')}" for s in results["skills"]) + ")")
    store.save("skills", state)
    return public_skills(state)


def reset_skills():
    store.save("skills", _empty_skills())
    return public_skills(_empty_skills())


def skills_for_profile():
    state = load_skills()
    if state["status"] != "graded":
        return None
    r = state["results"]
    per_q = [f"{q['area']} (level {q['level']}): {g['score']}/4: {q['question'][:90]}"
             for q, g in zip(state["questions"], r["grades"])]
    return {"score": f"{r['total']}/{r['max']}", "areas": r["skills"], "questions": per_q, "summary": r["summary"]}
