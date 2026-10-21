"""Typical US recruiting windows for summer internships, by field.

Months are relative to the internship summer (0 = January of that summer's year; -4 = September of
the year before). These are rough patterns from how student recruiting usually runs; they move a
little every year and differ by firm, so the page always says to check each firm's own dates."""
from datetime import date

FIELDS = [
    {"key": "ib", "label": "Investment banking (large banks, elite boutiques)",
     "match": r"invest\w* bank|\bib\b|m&a|capital markets|sales and trading|s&t",
     "windows": [(-12, -8, "Applications and first-round interviews (sophomore spring for a junior-year summer)"),
                 (-11, -6, "Superdays and offers, rolling")],
     "note": "Recruiting for the main junior-summer internship starts more than a year ahead. Networking (alumni calls) in the fall before matters a lot. Sophomore and diversity programs open earlier."},
    {"key": "boutique", "label": "Boutique banks, small PE, search funds, small VC",
     "match": r"boutique|search fund|private equity|\bpe\b|venture|\bvc\b|family office",
     "windows": [(-6, 5, "Cold outreach works year-round; reach out 1-3 months before you want to start"),
                 (-12, 5, "Part-time and remote internships during the semester, rolling")],
     "note": "No fixed cycle: they hire when they need help. The outreach playbook (10+ firms a day, follow up after 7 days) is how these get filled."},
    {"key": "consulting", "label": "Consulting (MBB, Big 4 strategy, boutiques)",
     "match": r"consult|strategy",
     "windows": [(-5, 1, "Applications for junior-summer internships"), (-3, 2, "Case interviews and offers")],
     "note": "Case interview practice takes weeks; start early in the fall. Some firms run sophomore programs in the spring before."},
    {"key": "swe", "label": "Software engineering (big tech and startups)",
     "match": r"software|swe\b|developer|engineer(ing)? intern|backend|frontend|full.?stack",
     "windows": [(-6, -3, "Big tech applications open (apply early: rolling review)"),
                 (-4, 2, "Online assessments and interviews"), (-2, 4, "Startups and mid-size companies keep hiring")],
     "note": "Applications open late summer. Online coding assessments come first, then 1-3 technical interviews."},
    {"key": "quant", "label": "Quant trading and research",
     "match": r"quant|trading|trader|market mak",
     "windows": [(-6, -3, "Applications (fills fast)"), (-5, -1, "Math, probability and coding assessments, then interviews")],
     "note": "One of the earliest cycles; many firms open applications in July or August."},
    {"key": "data", "label": "Data science and analytics",
     "match": r"data scien|data analy|analytics|machine learning|\bml\b|\bai\b",
     "windows": [(-5, 3, "Applications, rolling")],
     "note": "Big tech data roles follow the software timeline; analytics roles at other companies run through spring."},
    {"key": "pm", "label": "Product management",
     "match": r"product manag|\bapm\b|\bpm\b",
     "windows": [(-5, -2, "APM and PM intern applications")],
     "note": "Few spots; strong projects and referrals matter."},
    {"key": "accounting", "label": "Accounting (Big 4 audit, tax, advisory)",
     "match": r"account|audit|tax\b|cpa",
     "windows": [(-16, -12, "Leadership and early-identification programs (sophomore spring)"), (-8, -4, "Internship recruiting (junior fall)")],
     "note": "Big 4 internships usually come from early-identification programs and fall campus recruiting."},
    {"key": "marketing", "label": "Marketing and brand management",
     "match": r"marketing|brand|growth|communications",
     "windows": [(-5, -2, "Large consumer brands"), (-2, 4, "Agencies, startups and smaller companies")],
     "note": "Consumer-goods brand programs recruit in the fall; most other marketing roles hire in spring."},
    {"key": "biotech", "label": "Biotech, pharma and lab research",
     "match": r"biotech|pharma|biolog|chemist|lab\b|research assistant|life science|biochem",
     "windows": [(-4, 2, "Company internships"), (-4, 1, "Summer research programs (REUs) and campus lab positions")],
     "note": "REU programs have firm deadlines in winter; campus labs take students who email professors directly."},
    {"key": "engineering", "label": "Mechanical, electrical, civil and other engineering",
     "match": r"mechanical|electrical|civil|aerospace|industrial|chemical engineer|hardware",
     "windows": [(-4, -2, "Fall career fairs and applications"), (-1, 3, "Spring career fairs and rolling hiring")],
     "note": "Career fairs are central; bring printed resumes and follow up within a day."},
    {"key": "policy", "label": "Government, policy and nonprofits",
     "match": r"government|policy|nonprofit|public sector|ngo",
     "windows": [(-4, 1, "Applications (federal programs often close in winter)")],
     "note": "Federal internships need US citizenship in most cases."},
]

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _month(year, offset):
    """Month `offset` relative to January of `year` → (year, month index 0-11)."""
    total = year * 12 + offset
    return total // 12, total % 12


def _label(year, offset):
    y, m = _month(year, offset)
    return f"{MONTHS[m]} {y}"


def grad_year(profile):
    years = []
    for e in profile.get("education") or []:
        for k in ("end", "graduation", "grad_year"):
            v = str(e.get(k) or "")
            for tok in v.replace("–", " ").replace("-", " ").split():
                if tok.isdigit() and len(tok) == 4 and 2020 <= int(tok) <= 2040:
                    years.append(int(tok))
    return max(years) if years else None


SUMMER_NAMES = {1: "after junior year (the main internship)", 2: "after sophomore year", 3: "after freshman year",
                0: "after graduation (full-time)"}
SUMMER_ADVICE = {
    3: "Most big internship programs take juniors. For this summer, aim at small firms (search funds, boutiques, startups), campus research, "
       "and the few first-year programs some banks and tech companies run (often called early insight, explore or freshman programs; deadlines are early).",
    2: "Sophomore programs exist at many banks, consulting and tech firms, with earlier deadlines than junior internships. Small firms, startups "
       "and research are strong options too, and a good sophomore internship makes the junior search much easier.",
    1: "This is the main internship, and it often leads to a full-time offer. Many fields recruit for it a year or more ahead.",
    0: "Full-time recruiting mostly happens in the fall of senior year; many offers come from the junior-summer internship.",
}


def summers(profile, today=None):
    """Upcoming summers with what kind each one is, from the graduation year."""
    today = today or date.today()
    g = grad_year(profile)
    first = today.year if today.month <= 5 else today.year + 1
    out = []
    for y in range(first, first + 3):
        years_left = (g - y) if g else None
        if years_left is not None and years_left < 0:
            continue
        out.append({"year": y, "kind": SUMMER_NAMES.get(years_left, "") if years_left is not None else "",
                    "advice": SUMMER_ADVICE.get(years_left, "") if years_left is not None else ""})
    return out


def for_profile(profile, today=None):
    """Windows for the user's target fields (or all fields), for each upcoming summer."""
    import re
    today = today or date.today()
    t = profile.get("targets") or {}
    text = " ".join(str(x) for v in t.values() for x in (v if isinstance(v, list) else [v])).lower()
    picked = [f for f in FIELDS if re.search(f["match"], text)] or []
    now_index = today.year * 12 + today.month - 1
    rows = []
    for f in FIELDS:
        plans = []
        for s in summers(profile, today):
            for lo, hi, what in f["windows"]:
                start, end = s["year"] * 12 + lo, s["year"] * 12 + hi
                if end < now_index:
                    continue  # already over
                plans.append({"summer": s["year"], "summer_kind": s["kind"], "what": what, "from": _label(s["year"], lo),
                              "to": _label(s["year"], hi), "open_now": start <= now_index <= end, "starts_in_months": max(0, start - now_index)})
        rows.append({"key": f["key"], "label": f["label"], "note": f["note"], "yours": f in picked,
                     "windows": sorted(plans, key=lambda p: (p["summer"], p["starts_in_months"]))[:4]})
    rows.sort(key=lambda r: not r["yours"])
    return {"grad_year": grad_year(profile), "summers": summers(profile, today), "fields": rows,
            "caveat": "Typical US windows. They shift every year and differ by firm, so always check each firm's own dates."}
