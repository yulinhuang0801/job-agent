"""Company-specific interview prep: research a company's interview process and values, then add
tailored behavioral and technical questions to the user's own question bank."""
import json
import re
import uuid
from datetime import datetime

from . import assess, llm, prompts, services, store


def load_preps():
    return store.load("prep", [])


def build(company, role):
    company, role = company.strip(), role.strip()
    if not company:
        raise llm.LLMError("Which company are you preparing for?")
    material = []
    if services.search_enabled():
        for q in (f"{company} {role} interview questions", f"{company} interview process {role}",
                  f"{company} values principles culture"):
            found = services.web_search({"query": q, "max_results": 5})
            if found.get("answer"):
                material.append(f"Q: {q}\nSummary: {found['answer']}")
            material += [f"- {r['title']} ({r['url']}): {r['content']}" for r in found.get("results", [])]
    profile = store.load("profile", {})
    background = json.dumps({k: profile.get(k) for k in ("targets", "summary", "skills")}, ensure_ascii=False)[:3000]
    out = llm.chat_json([
        {"role": "system", "content": prompts.COMPANY_PREP_SYSTEM},
        {"role": "user", "content": f"COMPANY: {company}\nROLE: {role or 'not specified (use the candidate targets)'}\n\n"
                                    f"CANDIDATE:\n{background}\n\nSEARCH RESULTS:\n" + ("\n".join(material)[:20000] or "(none: no search key)")},
    ], max_tokens=16000, temperature=0.4, timeout=180)

    group = f"{company} prep"
    added_b, added_t = [], []
    for b in out.get("behavioral") or []:
        if b.get("question"):
            data = assess.add_custom("behavioral", {"label": b.get("label") or b["question"][:60], "question": b["question"],
                                                    "look_for": b.get("look_for", ""), "kind": b.get("kind") if b.get("kind") in ("story", "answer") else "story",
                                                    "group": group})
            added_b.append(data["behavioral"][-1]["id"])
    for t in out.get("technical") or []:
        if t.get("question"):
            data = assess.add_custom("technical", {"area": group, "level": t.get("level") or 2, "type": t.get("type") or "concept",
                                                   "question": t["question"], "rubric": t.get("rubric", ""), "lang": t.get("lang") or None})
            added_t.append(data["technical"][-1]["id"])

    prep = {"id": uuid.uuid4().hex[:8], "company": company, "role": role, "created_at": datetime.now().isoformat(timespec="seconds"),
            "area": group, "searched": bool(material),
            **{k: out.get(k) or [] for k in ("process", "values", "tips", "sources_used")},
            "summary": out.get("summary") or "", "behavioral_ids": added_b, "technical_ids": added_t}
    preps = [p for p in load_preps() if not (p["company"].lower() == company.lower() and p["role"].lower() == role.lower())]
    preps.insert(0, prep)
    store.save("prep", preps)
    return prep


def delete(prep_id):
    preps = load_preps()
    gone = next((p for p in preps if p["id"] == prep_id), None)
    if gone:
        for qid in gone["behavioral_ids"] + gone["technical_ids"]:
            assess.delete_custom(qid)
    store.save("prep", [p for p in preps if p["id"] != prep_id])
    return load_preps()
