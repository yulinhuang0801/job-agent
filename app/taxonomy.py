"""Handshake-style categories for firms and job postings, and rules that assign them.

Our own lists (Handshake's exact lists aren't public): industry, job function, job type, term,
location (state/country, remote), company size and work-authorization hints."""
import re

INDUSTRIES = [
    "Software & Internet", "AI & Machine Learning", "Fintech", "Hardware & Semiconductors", "Telecommunications",
    "Investment Banking", "Private Equity", "Venture Capital", "Hedge Funds & Asset Management", "Wealth Management",
    "Banking & Lending", "Insurance", "Real Estate", "Consulting", "Accounting",
    "Biotech & Pharma", "Healthcare", "Medical Devices", "Energy & Climate", "Manufacturing & Industrials",
    "Aerospace & Defense", "Automotive & Transportation", "Consumer & Retail", "Media & Entertainment", "Gaming",
    "Education", "Government & Nonprofit", "Legal", "Agriculture & Food", "Other",
]

FUNCTIONS = [
    "Software Engineering", "Data Science & Analytics", "Machine Learning & AI", "Product Management", "Design (UX/UI)",
    "Hardware Engineering", "Mechanical & Aerospace Engineering", "Electrical Engineering", "Civil & Construction",
    "Science & Research", "Quantitative Finance", "Investment Banking & Finance", "Accounting", "Consulting & Strategy",
    "Marketing", "Sales & Business Development", "Operations & Supply Chain", "People & Recruiting", "Legal & Policy",
    "Customer Success & Support", "IT & Security", "Healthcare & Clinical", "Communications & Content", "Other",
]

JOB_TYPES = ["Internship", "Co-op", "New grad / Entry level", "Full-time", "Part-time", "Contract"]

SIZES = [("1-10", 1, 10), ("11-30", 11, 30), ("31-100", 31, 100), ("101-500", 101, 500), ("500+", 501, 10**9)]

# Ordered: the first matching rule wins. Matched against "title | department".
_FUNCTION_RULES = [
    ("Quantitative Finance", r"\bquant|trading|trader|algorithmic"),
    ("Machine Learning & AI", r"machine learning|\bml\b|\bai\b|deep learning|llm|computer vision|\bnlp\b|research scientist"),
    ("Data Science & Analytics", r"data scien|data analy|analytics|business intelligence|\bbi\b|data engineer|statistic"),
    ("Product Management", r"product manag|\bpm\b|product owner|program manag"),
    ("Design (UX/UI)", r"design|\bux\b|\bui\b|user research"),
    ("IT & Security", r"security|\bit\b|information technology|devops|site reliability|\bsre\b|network|infrastructure|helpdesk|help desk|systems admin"),
    ("Software Engineering", r"software|engineer.*(backend|frontend|full.?stack|platform|mobile|web|ios|android)|developer|swe\b|programmer|full.?stack|front.?end|back.?end"),
    ("Hardware Engineering", r"hardware|fpga|asic|silicon|chip|embedded|firmware|rf\b|semiconductor"),
    ("Electrical Engineering", r"electrical|power systems|controls engineer"),
    ("Mechanical & Aerospace Engineering", r"mechanical|aerospace|propulsion|structures|manufacturing engineer|robotics"),
    ("Civil & Construction", r"civil|construction|structural|architect(?!ure)"),
    ("Science & Research", r"biolog|chemist|scien|lab |laboratory|research associate|clinical research|bioinformatic|physic"),
    ("Healthcare & Clinical", r"nurse|clinic|medical|physician|pharmac|therap|patient"),
    ("Investment Banking & Finance", r"investment bank|analyst.*(finance|investment|m&a)|finance|financial|treasury|private equity|venture|credit|underwrit|fp&a|investment"),
    ("Accounting", r"account|audit|tax\b|controller|bookkeep"),
    ("Consulting & Strategy", r"consult|strategy|strategic"),
    ("Marketing", r"marketing|growth|brand|seo|social media|content market"),
    ("Sales & Business Development", r"sales|business develop|account exec|partnership|\bbd\b|\bsdr\b|\bbdr\b"),
    ("Operations & Supply Chain", r"operation|supply chain|logistic|procurement|technician|assembly|production|facilities"),
    ("People & Recruiting", r"recruit|talent|people|\bhr\b|human resources"),
    ("Legal & Policy", r"legal|counsel|policy|compliance|regulatory|paralegal"),
    ("Customer Success & Support", r"customer|support|success|client service"),
    ("Communications & Content", r"communicat|writer|editor|content|public relations|\bpr\b"),
    ("Software Engineering", r"engineer"),  # generic engineers at tech companies
]


def classify_function(title, department=""):
    text = f"{title} | {department}".lower()
    for name, pattern in _FUNCTION_RULES:
        if re.search(pattern, text):
            return name
    return "Other"


def classify_job_type(title, commitment=""):
    t = f"{title} {commitment}".lower()
    if re.search(r"co-?op\b", t):
        return "Co-op"
    if re.search(r"\bintern(ship)?\b|\bsummer (analyst|associate)\b|working student|apprentice", t):
        return "Internship"
    if re.search(r"new grad|entry.level|graduate (program|engineer|analyst)|early career|university grad|campus|junior|associate i\b|\bi\b$", t):
        return "New grad / Entry level"
    if re.search(r"part.?time", t):
        return "Part-time"
    if re.search(r"contract|temporary|temp\b|freelance", t):
        return "Contract"
    return "Full-time"


_TERMS = [("Summer", r"summer"), ("Fall", r"\bfall\b|autumn"), ("Winter", r"winter"), ("Spring", r"spring")]


def classify_term(*texts):
    t = " ".join(texts).lower()
    year = re.search(r"20(2[6-9]|3\d)", t)
    for name, pattern in _TERMS:
        if re.search(pattern, t):
            return f"{name} {year.group(0)}" if year else name
    return ""


US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
_STATE_NAMES = {v.lower(): k for k, v in US_STATES.items()}
_CITY_STATE = {"nyc": "NY", "new york": "NY", "sf": "CA", "san francisco": "CA", "bay area": "CA", "los angeles": "CA", "la": "CA",
               "seattle": "WA", "boston": "MA", "chicago": "IL", "austin": "TX", "dallas": "TX", "houston": "TX", "atlanta": "GA",
               "denver": "CO", "miami": "FL", "washington dc": "DC", "washington, dc": "DC", "palo alto": "CA", "mountain view": "CA",
               "san jose": "CA", "san diego": "CA", "philadelphia": "PA", "pittsburgh": "PA", "minneapolis": "MN"}


def parse_location(location):
    """Return (state code or '', country, remote flag) from a free-text location."""
    loc = (location or "").strip()
    low = loc.lower()
    remote = bool(re.search(r"remote|anywhere|distributed|work from home", low))
    m = re.search(r",\s*([A-Z]{2})\b", loc)
    if m and m.group(1) in US_STATES:
        return m.group(1), "United States", remote
    for name, code in _STATE_NAMES.items():
        if re.search(rf"\b{name}\b", low):
            return code, "United States", remote
    for city, code in _CITY_STATE.items():
        if re.search(rf"\b{re.escape(city)}\b", low):
            return code, "United States", remote
    if re.search(r"united states|\busa?\b|u\.s\.", low):
        return "", "United States", remote
    for country in ("Canada", "United Kingdom", "UK", "Germany", "France", "India", "Singapore", "China", "Japan", "Australia",
                    "Ireland", "Netherlands", "Israel", "Switzerland", "Spain", "Poland", "Brazil", "Mexico"):
        if country.lower() in low:
            return "", "United Kingdom" if country == "UK" else country, remote
    return "", "", remote


def sponsorship_hint(text):
    t = (text or "").lower()
    if re.search(r"(not|unable to|cannot|can't|will not|won't|no) (be able to )?(provide |offer )?(visa )?sponsor|without (the need for )?(visa )?sponsorship|no sponsorship", t):
        return "No sponsorship"
    if re.search(r"\b(opt|cpt|f-1|h-1b|visa sponsorship (is )?available|will sponsor)\b", t):
        return "Mentions visas"
    return ""


def size_bucket(employees):
    if employees is None:
        return ""
    for label, lo, hi in SIZES:
        if lo <= employees <= hi:
            return label
    return ""


# ---------- SEC data → our industries ----------

FORM_D_INDUSTRY = {
    "Biotechnology": "Biotech & Pharma", "Pharmaceuticals": "Biotech & Pharma", "Other Health Care": "Healthcare",
    "Health Insurance": "Insurance", "Hospitals and Physicians": "Healthcare", "Other Technology": "Software & Internet",
    "Computers": "Hardware & Semiconductors", "Telecommunications": "Telecommunications", "Commercial Banking": "Banking & Lending",
    "Investment Banking": "Investment Banking", "Insurance": "Insurance", "Investing": "Hedge Funds & Asset Management",
    "Other Banking and Financial Services": "Fintech", "Other Real Estate": "Real Estate", "Commercial": "Real Estate",
    "Residential": "Real Estate", "REITS and Finance": "Real Estate", "Construction": "Real Estate",
    "Oil and Gas": "Energy & Climate", "Coal Mining": "Energy & Climate", "Electric Utilities": "Energy & Climate",
    "Energy Conservation": "Energy & Climate", "Environmental Services": "Energy & Climate", "Other Energy": "Energy & Climate",
    "Manufacturing": "Manufacturing & Industrials", "Airlines and Airports": "Automotive & Transportation",
    "Lodging and Conventions": "Consumer & Retail", "Tourism and Travel Services": "Consumer & Retail",
    "Other Travel": "Consumer & Retail", "Restaurants": "Consumer & Retail", "Retailing": "Consumer & Retail",
    "Agriculture": "Agriculture & Food", "Business Services": "Consulting", "Other": "Other",
}
FORM_D_FUND_TYPES = {"Private Equity Fund": "Private Equity", "Venture Capital Fund": "Venture Capital",
                     "Hedge Fund": "Hedge Funds & Asset Management", "Other Investment Fund": "Hedge Funds & Asset Management"}
