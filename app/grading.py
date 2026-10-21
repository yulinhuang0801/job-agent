"""Rubric grading for behavioral answers, with checked evidence.

Each answer is scored 1-4 on fixed dimensions, using written anchors for every level and two
worked examples, at temperature 0. The grader must quote the candidate's own words as evidence;
code checks every quote against the answer, and a score of 3 or 4 without a real quote is capped
at 2. Grades are pure functions here (no storage), so the calibration set in tests/calibration
can run them against expected scores."""
import hashlib
import json
import re

from . import llm

RUBRIC_VERSION = "stories-v1"

STORY_DIMS = [
    ("situation", "Situation & stakes", 1,
     {1: "Missing, or generic (\"in a group project\", \"at my internship\").",
      2: "Names the setting but not why it mattered.",
      3: "Specific setting (who, what, when) and why it mattered.",
      4: "Specific setting plus concrete stakes or constraints (deadline, people affected, numbers), told briefly."}),
    ("action", "Your actions", 2,
     {1: "Only \"we\", or vague (\"worked hard\", \"communicated\").",
      2: "Some of their own actions, but generic.",
      3: "Two or more specific steps they personally took.",
      4: "Specific steps they personally took, including a decision or trade-off and why they made it."}),
    ("result", "Result", 2,
     {1: "No result.",
      2: "Vague result (\"it went well\", \"the team was happy\").",
      3: "Concrete outcome: what changed because of them.",
      4: "Measurable outcome (a number, a before/after) or outside recognition, tied back to the goal."}),
    ("reflection", "Reflection", 1,
     {1: "No lesson.",
      2: "A cliché (\"I learned teamwork matters\").",
      3: "A specific lesson from this experience.",
      4: "A specific lesson and how they used it later, or what they'd do differently."}),
    ("fit", "Answers the question", 1,
     {1: "Doesn't answer the question asked.",
      2: "Loosely related.",
      3: "Clearly answers it.",
      4: "Clearly answers it and shows the exact quality the question tests."}),
]

ANSWER_DIMS = [
    ("relevance", "Answers the question", 1,
     {1: "Doesn't answer it.", 2: "Partly answers it, or wanders.", 3: "Answers it directly.",
      4: "Answers it directly in the first sentence."}),
    ("specifics", "Specific evidence", 2,
     {1: "Only general claims (\"I'm hardworking\").", 2: "One vague example.", 3: "At least one specific example, name or fact.",
      4: "Specific examples or numbers that prove each claim."}),
    ("structure", "Clear structure", 1,
     {1: "Hard to follow.", 2: "Some order, but rambles or repeats.", 3: "A clear point with support.",
      4: "Point first, two or three supporting points, a clean close."}),
    ("tailoring", "Personal and tailored", 1,
     {1: "Could be anyone's answer.", 2: "A little personal.", 3: "Clearly theirs.",
      4: "Clearly theirs and linked to the role or field they're targeting."}),
]

EXAMPLES = """Worked examples (for calibration; don't grade these):

A) Question: "Tell me about a time you resolved a conflict." Answer: "In a group project we disagreed about the topic. We talked about it and compromised, and we got a good grade. I learned communication is important."
→ situation 2 (setting, no stakes), action 1 (only "we"), result 2 ("a good grade" is vague), reflection 2 (cliché), fit 2.

B) Question: "Tell me about a time you resolved a conflict." Answer: "Two weeks before our CS 222 demo, my teammate wanted to rewrite our backend in Go while I owned the Flask API. I asked him to list what Go would fix, then benchmarked our three slowest endpoints: two were slow because of missing database indexes, not Python. I added the indexes, cut response time from 900 ms to 120 ms, and we agreed to keep Flask. We demoed on time and got 98/100. Now I bring data to disagreements before arguing."
→ situation 4, action 4 (specific steps and a decision with a reason), result 4 (numbers), reflection 4 (applied later), fit 4."""


def _rubric_text(dims):
    out = []
    for key, label, _, anchors in dims:
        out.append(f"- {key} ({label}): " + " | ".join(f"{lvl} = {txt}" for lvl, txt in anchors.items()))
    return "\n".join(out)


GRADE_SYSTEM = """You grade behavioral interview answers against a fixed rubric, like a calibrated hiring-committee reviewer. Output a json object only.

Rubric for STAR stories (kind "story"), each dimension scored 1-4:
{story_rubric}

Rubric for short answers (kind "answer"), each dimension scored 1-4:
{answer_rubric}

{examples}

Rules:
- Use the anchors exactly. Score what is written, not what the candidate might have done. Don't reward length or polish; don't penalize informal English, typos, or answers in Chinese.
- For every dimension, copy the candidate's exact words that justify the score into "quote" (character for character, at most 25 words). If the evidence is missing, leave quote "" and score 1 or 2. A 3 or 4 always needs a quote.
- "why": one short sentence naming what earns the score or what's missing.
- "top_fix": the single change that would raise the overall grade most, specific to this answer (e.g. "Add how many users the fix reached"), never generic advice.
- Write why and top_fix in the language given in "write_feedback_in" for that answer, even if other answers in the batch use another language.

JSON shape:
{{"grades": [{{"id": "", "scores": {{"<dimension key>": {{"score": <1-4>, "quote": "", "why": ""}}}}, "top_fix": ""}}]}}"""


def dims_for(kind):
    return STORY_DIMS if kind == "story" else ANSWER_DIMS


def answer_text(item):
    """The answer as one text, in STAR order for stories."""
    if item.get("kind") == "story":
        parts = [(k.capitalize(), item.get(k, "")) for k in ("situation", "task", "action", "result", "reflection")]
        return "\n".join(f"{k}: {v}" for k, v in parts if v)
    return item.get("answer", "")


def language(text):
    """Chinese when there are more Chinese characters than English words, else English."""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    words = len(re.findall(r"[A-Za-z]+", text))
    return "Chinese" if cjk > words else "English"


def text_hash(item):
    return hashlib.sha1(answer_text(item).encode("utf-8")).hexdigest()[:12]


def _norm(s):
    s = re.sub(r"[“”\"'‘’`]", "", s.lower())
    return re.sub(r"\s+", " ", s).strip(" .,;:!?-–—")


def quote_found(quote, text):
    """True when the quote (or each part of it around '...') appears in the answer."""
    q = _norm(quote)
    if not q:
        return False
    body = _norm(text)
    parts = [p.strip() for p in re.split(r"\.\.\.|…", q) if len(p.strip()) >= 3]
    return bool(parts) and all(p in body for p in parts)


def stars(scores, dims):
    """Weighted rubric total → 1-5 stars (all 1s = 1 star, all 4s = 5 stars)."""
    total = sum(w * scores.get(k, 1) for k, _, w, _ in dims)
    lo, hi = sum(w for _, _, w, _ in dims), 4 * sum(w for _, _, w, _ in dims)
    return 1 + round(4 * (total - lo) / (hi - lo)), total, hi


def _finish(item, raw):
    dims = dims_for(item["kind"])
    text = answer_text(item)
    out, scores = [], {}
    raw_scores = (raw or {}).get("scores") or {}
    for key, label, weight, _ in dims:
        d = raw_scores.get(key) or {}
        try:
            score = max(1, min(4, int(d.get("score") or 1)))
        except (TypeError, ValueError):
            score = 1
        quote = str(d.get("quote") or "").strip()
        verified = quote_found(quote, text) if quote else False
        why = str(d.get("why") or "").strip()
        if score >= 3 and not verified:
            score = 2
            why = (why + " " if why else "") + "(Capped at 2: the grader's quote wasn't found in your answer.)"
        scores[key] = score
        out.append({"key": key, "label": label, "score": score, "quote": quote if verified else "", "why": why})
    n_stars, total, max_total = stars(scores, dims)
    words = len(re.findall(r"\w+", text))
    return {"version": RUBRIC_VERSION, "dims": out, "stars": n_stars, "total": total, "max": max_total,
            "top_fix": str((raw or {}).get("top_fix") or "").strip(), "words": words, "hash": text_hash(item)}


def grade(items):
    """items: [{id, kind ("story"|"answer"), question, look_for, situation..reflection | answer}] → {id: grade}.
    Graded six at a time at temperature 0."""
    results = {}
    system = GRADE_SYSTEM.format(story_rubric=_rubric_text(STORY_DIMS), answer_rubric=_rubric_text(ANSWER_DIMS), examples=EXAMPLES)
    todo = [i for i in items if answer_text(i).strip()]
    for start in range(0, len(todo), 6):
        batch = todo[start:start + 6]
        payload = [{"id": i["id"], "kind": i["kind"], "question": i.get("question", ""), "what the question tests": i.get("look_for", ""),
                    "answer": answer_text(i), "write_feedback_in": language(answer_text(i))} for i in batch]
        out = llm.chat_json([
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ], max_tokens=8000, temperature=0, think=False, timeout=180)
        got = {str(g.get("id")): g for g in out.get("grades") or []}
        for i in batch:
            results[i["id"]] = _finish(i, got.get(str(i["id"])))
    return results


# ---------- "Was this fair?" ----------
# Every verdict is saved with the question, the answer and the grade. Over time this becomes the
# labelled data a better grader needs (none exists publicly for behavioral answers).

FEEDBACK_FILE = "grade_feedback.jsonl"


def record_feedback(entry):
    from . import store
    from datetime import datetime
    store.DATA_DIR.mkdir(parents=True, exist_ok=True)
    entry = {**entry, "at": datetime.now().isoformat(timespec="seconds")}
    with open(store.DATA_DIR / FEEDBACK_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry
