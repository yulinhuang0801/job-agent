"""Update an existing profile without losing anything.

The AI proposes a patch (fields to fill, items to add, bullets to add, skill levels). Code applies
it with three guarantees: nothing is deleted, items are matched by identity (company + title,
project name, school), and fields pinned because the candidate or a coach edited them are never
changed."""
import json

from . import llm, memory, prompts

SCALARS = ("name", "email", "phone", "location", "summary")
LIST_SECTIONS = ("experience", "projects", "education")
IDENTITY = {"company", "title", "name", "school"}  # how items are matched; never changed by a merge


def item_key(section, item):
    if section == "experience":
        return f"{item.get('company', '')}::{item.get('title', '')}".strip().lower()
    if section == "projects":
        return str(item.get("name", "")).strip().lower()
    if section == "education":
        return str(item.get("school", "")).strip().lower()
    return ""


def pin_key(section, item, field):
    return f"{section}::{item_key(section, item)}::{field}"


def path_to_pin(profile, path):
    """Turn an edit path like "experience.1.bullets.2" into a pin like "experience::acme::intern::bullets"."""
    parts = path.split(".")
    if parts[0] in SCALARS:
        return parts[0]
    if parts[0] == "targets" and len(parts) > 1:
        return f"targets.{parts[1]}"
    if parts[0] in LIST_SECTIONS and len(parts) > 2:
        try:
            item = profile[parts[0]][int(parts[1])]
        except (KeyError, IndexError, ValueError):
            return ""
        return pin_key(parts[0], item, parts[2])
    if parts[0] == "skills" and len(parts) > 2 and parts[1] == "technical":
        try:
            return f"skills::{profile['skills']['technical'][int(parts[2])].get('name', '').lower()}"
        except (KeyError, IndexError, ValueError, AttributeError):
            return ""
    return parts[0]


def diff_pins(old, new):
    """Pins for every field the candidate changed in a hand edit of the whole profile."""
    pins = [k for k in SCALARS if old.get(k) != new.get(k)]
    ot, nt = old.get("targets") or {}, new.get("targets") or {}
    pins += [f"targets.{k}" for k in set(ot) | set(nt) if ot.get(k) != nt.get(k)]
    for section in LIST_SECTIONS:
        before = {item_key(section, i): i for i in old.get(section) or []}
        for item in new.get(section) or []:
            prev = before.get(item_key(section, item))
            for field, value in item.items():
                if prev is None or prev.get(field) != value:
                    pins.append(pin_key(section, item, field))
    os_, ns = (old.get("skills") or {}), (new.get("skills") or {})
    before = {str(s.get("name", "")).lower(): s for s in os_.get("technical") or [] if isinstance(s, dict)}
    for s in ns.get("technical") or []:
        if isinstance(s, dict) and before.get(str(s.get("name", "")).lower()) != s:
            pins.append(f"skills::{str(s.get('name', '')).lower()}")
    return pins


def _empty(v):
    return v in (None, "", [], {})


def apply_patch(profile, patch, pinned, guard_similar=True):
    changes = []
    for field, value in (patch.get("set") or {}).items():
        if field in SCALARS and not _empty(value) and field not in pinned and profile.get(field) != value:
            profile[field] = value
            changes.append(f"Updated {field}")
        elif field == "targets" and isinstance(value, dict):
            targets = profile.setdefault("targets", {})
            for k, v in value.items():
                if not _empty(v) and f"targets.{k}" not in pinned and targets.get(k) != v:
                    targets[k] = v
                    changes.append(f"Updated target: {k}")

    add = patch.get("add") or {}
    # Everything already listed anywhere (by company, project name or school), to catch cross-section duplicates.
    listed = {str(i.get(k, "")).strip().lower() for sec in LIST_SECTIONS for i in profile.get(sec) or []
              for k in ("company", "name", "school") if i.get(k)}
    for section in LIST_SECTIONS:
        have = {item_key(section, i) for i in profile.get(section) or []}
        for item in add.get(section) or []:
            label_key = str(item.get("company") or item.get("name") or item.get("school") or "").strip().lower() if isinstance(item, dict) else ""
            similar = any(label_key and (label_key in x or x in label_key) and len(x) > 5 for x in listed)
            if isinstance(item, dict) and item_key(section, item) and item_key(section, item) not in have and not (guard_similar and similar and section != "education"):
                profile.setdefault(section, []).append(item)
                have.add(item_key(section, item))
                label = item.get("company") or item.get("name") or item.get("school")
                changes.append(f"Added {({'experience': 'experience', 'projects': 'project', 'education': 'education'})[section]}: {label}")
    links = profile.setdefault("links", [])
    for link in add.get("links") or []:
        url = (link.get("url") if isinstance(link, dict) else str(link)) or ""
        if url and url not in {(l.get("url") if isinstance(l, dict) else l) for l in links}:
            links.append(link)
            changes.append(f"Added link: {url}")
    skills = profile.setdefault("skills", {})
    for key in ("tools", "spoken_languages"):
        for v in add.get(key) or []:
            if v and v not in skills.setdefault(key, []):
                skills[key].append(v)
                changes.append(f"Added {key.replace('_', ' ')}: {v}")

    for u in patch.get("update") or []:
        section = u.get("section")
        if section not in LIST_SECTIONS:
            continue
        match = str(u.get("match") or "").strip().lower()
        item = next((i for i in profile.get(section) or []
                     if match and (match == item_key(section, i) or match in item_key(section, i))), None)
        if not item:
            continue
        label = item.get("company") or item.get("name") or item.get("school")
        for field, value in (u.get("set") or {}).items():
            if field != "bullets" and field not in IDENTITY and not _empty(value) and pin_key(section, item, field) not in pinned and item.get(field) != value:
                item[field] = value
                changes.append(f"Updated {label}: {field}")
        new_bullets = [b for b in u.get("add_bullets") or [] if b and b not in (item.get("bullets") or [])]
        if new_bullets and pin_key(section, item, "bullets") not in pinned:
            item.setdefault("bullets", []).extend(new_bullets)
            changes.append(f"Added {len(new_bullets)} bullet(s) to {label}")

    tech = skills.setdefault("technical", [])
    for s in patch.get("skill_levels") or []:
        name = str(s.get("name") or "").strip()
        if not name or f"skills::{name.lower()}" in pinned:
            continue
        current = next((x for x in tech if isinstance(x, dict) and str(x.get("name", "")).lower() == name.lower()), None)
        if current is None:
            tech.append({"name": name, "level": s.get("level", ""), "verified": bool(s.get("verified")), "evidence": s.get("evidence", "")})
            changes.append(f"Added skill: {name}")
        elif any(s.get(k) not in (None, "") and current.get(k) != s.get(k) for k in ("level", "verified", "evidence")):
            current.update({k: s[k] for k in ("level", "verified", "evidence") if s.get(k) not in (None, "")})
            changes.append(f"Updated skill: {name}")

    if patch.get("evaluation"):
        profile["evaluation"] = patch["evaluation"]
    return profile, changes


def merge(profile, material):
    """Ask the AI for a patch against the current profile, then apply it safely."""
    pinned = memory.pins()
    current = {k: v for k, v in profile.items() if k not in ("behavioral_stories", "prepared_answers")}
    patch = llm.chat_json([
        {"role": "system", "content": prompts.PROFILE_MERGE_SYSTEM},
        {"role": "user", "content": "CURRENT PROFILE:\n" + json.dumps(current, ensure_ascii=False)
                                    + "\n\nPROTECTED FIELDS (edited by hand; never change):\n" + (", ".join(sorted(pinned)) or "(none)")
                                    + "\n\nNEW MATERIAL:\n" + material},
    ], max_tokens=16000, temperature=0.2, think=False, timeout=240)
    profile, changes = apply_patch(profile, patch, pinned)
    return profile, changes
