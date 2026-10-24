"""One scoring model for the whole app.

- Bands: every score is read on the same 0-100 scale with the same words and colors: Strong (80+), Good (65-79),
  Stretch (50-64), Weak (under 50). Fit scores, Home match scores, stories and the skills check all use them.
- Job fit (Apply) and Home matches use the same rubric: the AI rates four parts 0-4 with a reason, and this module
  computes the 0-100 from the weights, so the AI never picks the number. Work authorization is a gate, not points:
  clearly not eligible caps the score at 40; unclear adds a flag. Without the posting text, "requirements met" can't be
  judged, so it counts as a middle 2 of 4 and the score is marked as an estimate.
- Stories and the skills check keep their own rubrics; their points map onto the same bands.
"""

BANDS = [(80, "Strong", "ok"), (65, "Good", "good"), (50, "Stretch", "warn"), (0, "Weak", "bad")]
INELIGIBLE_CAP = 40

# key, label, weight, what 0..4 mean
FIT_DIMS = [
    ("role", "Role and field match", 2,
     "4 exactly the role and field they target; 3 same field, different specialty; 2 an adjacent field they could move into; "
     "1 loosely related; 0 unrelated"),
    ("requirements", "Requirements met", 3,
     "count the preferred / nice-to-have list too, since that's what separates applicants. First list the required and preferred items to "
     "yourself, then: 4 meets every required item and nearly all preferred ones, with clear evidence (projects, experience) in the profile; "
     "3 meets every required item AND at least half the preferred ones; 2 meets the required items but fewer than half the preferred ones, "
     "or misses one required item; 1 misses several required items; 0 lacks the core requirements. If the posting has no preferred list, "
     "judge from the required items alone. null only if there's no posting text at all"),
    ("level", "Level and timing", 1,
     "4 right level and timing for them (e.g. an internship for a current student, their season and class year); 3 a small mismatch "
     "(asks for a later class year but is open); 2 clearly above their level; 1 well above; 0 wrong level (senior or experienced hire); "
     "null if not stated"),
    ("logistics", "Location and hours", 1,
     "4 location or remote setup and hours fit what they asked for; 3 a minor compromise; 2 a notable one (relocating for a summer, or "
     "hybrid in a city they'd have to move to); 1 on-site or hybrid when they asked for remote only, or hours they can't do; "
     "0 impossible as stated; null if not stated"),
]
FIT_WEIGHTS = {k: w for k, _, w, _ in FIT_DIMS}
FIT_LABELS = {k: l for k, l, _, _ in FIT_DIMS}


def band(score):
    """{'score', 'label', 'tone'} for a 0-100 score (None stays None)."""
    if score is None:
        return None
    s = max(0, min(100, round(score)))
    for floor, label, tone in BANDS:
        if s >= floor:
            return {"score": s, "label": label, "tone": tone}


def band_from_points(total, lowest, highest):
    """Rubric points → the same 0-100 bands (the lowest possible total is 0, the highest 100)."""
    if highest <= lowest:
        return None
    return band(100 * (total - lowest) / (highest - lowest))


def rubric_text():
    """The rubric, for prompts. Both the fit check and the daily matcher read exactly this."""
    lines = ["Rate each part 0-4 (an integer) or null when it truly can't be judged, with a one-line reason using only facts "
             "from the posting and the profile:"]
    for key, label, weight, anchors in FIT_DIMS:
        lines.append(f'- {key} ({label}, weight {weight}): {anchors}')
    lines.append("Write each reason to the candidate as \"you\". Work authorization is NOT one of the parts: it's judged separately as a gate.")
    return "\n".join(lines)


def _clean_dims(raw):
    out = {}
    for key, label, weight, _ in FIT_DIMS:
        d = (raw or {}).get(key)
        if isinstance(d, dict):
            val, why = d.get("score"), str(d.get("why") or "")[:300]
        else:
            val, why = d, ""
        try:
            val = None if val is None or val == "" else max(0, min(4, int(round(float(val)))))
        except (TypeError, ValueError):
            val = None
        out[key] = {"label": label, "weight": weight, "score": val, "why": why}
    return out


RESTRICTED = ("citizen", "permanent resident", "clearance")  # postings that exclude most international students
CONDITIONAL = ("no visa sponsorship", "authorized", "authorization")  # may still work with CPT/OPT: check


def gate_from_work_authorization(wa, international=None):
    """'ok', 'unclear' or 'blocked'. Decided by rule from what the posting requires (read by the AI) and the candidate's
    status (from the profile), not by the AI's yes/no, which flips between runs. international: True, False or None (unknown)."""
    wa = wa or {}
    req = str(wa.get("requirement") or "").lower()
    evidence = str(wa.get("evidence") or "").lower()
    if any(w in req for w in RESTRICTED) and evidence and not any(w in evidence for w in ("citizen", "permanent resident", "green card", "clearance")):
        req = "must be authorized to work in the US"  # the posting's own words don't say citizens-only: treat it as conditional
    if international is None:  # no status known: fall back to the AI's own call
        if wa.get("ok_for_candidate") is False:
            return "blocked"
        return "unclear" if req and req not in ("none stated", "sponsorship available", "cpt/opt welcome") else "ok"
    if not international:
        return "ok"
    if any(w in req for w in RESTRICTED):
        return "blocked"
    if any(w in req for w in CONDITIONAL):
        return "unclear"
    return "ok"


UNKNOWN_REQUIREMENTS = 2  # without the posting text, assume a middle "requirements met" rather than dropping the heaviest part


def compute(raw_dims, gate="ok", has_posting=True):
    """The score from the parts: {'score', 'band', 'dims', 'gate', 'estimate', 'capped'}."""
    dims = _clean_dims(raw_dims)
    if dims["requirements"]["score"] is None and any(d["score"] is not None for d in dims.values()):
        dims["requirements"]["assumed"] = UNKNOWN_REQUIREMENTS  # used in the score; shown as "not judged"
    known = [d for d in dims.values() if d["score"] is not None or d.get("assumed") is not None]
    if not known:
        return {"score": None, "band": None, "dims": dims, "gate": gate, "estimate": True, "capped": False}
    got = sum(d["weight"] * (d["score"] if d["score"] is not None else d["assumed"]) for d in known)
    best = sum(d["weight"] * 4 for d in known)
    score = round(100 * got / best)
    capped = gate == "blocked" and score > INELIGIBLE_CAP
    if capped:
        score = INELIGIBLE_CAP
    estimate = not has_posting or dims["requirements"]["score"] is None
    return {"score": score, "band": band(score), "dims": dims, "gate": gate, "estimate": estimate, "capped": capped}


def requirements_from_checklist(items):
    """The requirements part, counted from the fit check's list of the posting's qualifications: (score 0-4, reason) or (None, "").
    Counting what's met is far more repeatable than asking the AI for a rating."""
    items = [i for i in items or [] if isinstance(i, dict) and str(i.get("item") or "").strip()]
    req = [i for i in items if str(i.get("kind")).lower() == "required"]
    pref = [i for i in items if str(i.get("kind")).lower() != "required"]
    if not items:
        return None, ""
    req_met = sum(1 for i in req if i.get("met") is True)
    pref_met = sum(1 for i in pref if i.get("met") is True)
    missing = len(req) - req_met
    ratio = pref_met / len(pref) if pref else 1.0
    if missing == 0:
        score = 4 if ratio >= 0.85 else 3 if ratio >= 0.5 else 2
    elif missing == 1:
        score = 2 if len(req) > 2 else 1
    elif missing < len(req):
        score = 1
    else:
        score = 0
    why = f"You meet {req_met} of {len(req)} required" + (f" and {pref_met} of {len(pref)} preferred" if pref else "") + " qualifications."
    return score, why


def role_rule(dims, job_function, target_functions):
    """Role and field match: 4 when the job's function is one the candidate targets (the same functions the daily matcher
    filters on); otherwise the AI's rating, at most 3. Removes the 3-vs-4 coin flip on in-field jobs."""
    if not target_functions or not job_function or job_function == "Other":
        return dims
    dims = dict(dims or {})
    raw = dims.get("role")
    why = raw.get("why", "") if isinstance(raw, dict) else ""
    val = raw.get("score") if isinstance(raw, dict) else raw
    if job_function in target_functions:
        dims["role"] = {"score": 4, "why": why or f"{job_function} is one of the fields you target."}
    else:
        try:
            dims["role"] = {"score": min(3, int(val)), "why": why}
        except (TypeError, ValueError):
            pass
    return dims


def verdict(result):
    if result["gate"] == "blocked":
        return "Not eligible as posted"
    b = result["band"]
    return {"Strong": "Strong fit", "Good": "Good fit", "Stretch": "Stretch", "Weak": "Weak fit"}[b["label"]] if b else "Not enough to judge"


def finish_fit(fit, posting_chars, job_function="", target_functions=(), international=None):
    """Turn the fit check's parts into the score, band, verdict and breakdown the app shows."""
    dims = role_rule(fit.get("dims") or {}, job_function, target_functions)
    req_score, req_why = requirements_from_checklist(fit.get("requirements_checklist"))
    if req_score is not None:
        dims["requirements"] = {"score": req_score, "why": req_why}
    result = compute(dims, gate_from_work_authorization(fit.get("work_authorization"), international), posting_chars >= 300)
    fit["dims"] = result["dims"]
    fit["score"] = result["score"]
    fit["band"] = result["band"]
    fit["gate"] = result["gate"]
    fit["estimate"] = result["estimate"]
    fit["capped"] = result["capped"]
    fit["verdict"] = verdict(result)
    fit["scoring"] = "rubric-v1"
    return fit
