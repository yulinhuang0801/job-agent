"""Official sources without the network: the robots.txt matcher, never fetching a blocked page, the employer
registry, O*NET matching on a tiny local database, and competency tags with checked quotes."""
import os
import sys
import tempfile
from pathlib import Path

os.environ["JOB_AGENT_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[2]))

from app import grading, official, onet  # noqa: E402


def ok(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    assert cond, name


rb = "User-agent: AhrefsBot\nDisallow: /\n\nUser-agent: *\nDisallow: /internal\nDisallow: */noindex/*\nAllow: /internal/public$\nDisallow: /*.pdf$\n"
for url, want in [("https://x.com/content", True), ("https://x.com/internal/a", False), ("https://x.com/internal/public", True),
                  ("https://x.com/a/noindex/b", False), ("https://x.com/f.pdf", False), ("https://x.com/f.pdf?v=1", True)]:
    ok(f"robots {url}", official.robots_allows(rb, url) == want)
ok("robots: named bot group", official.robots_allows(rb, "https://x.com/content", agent="ahrefsbot") is False)
ok("robots: empty Disallow allows", official.robots_allows("User-agent: *\nDisallow:\n", "https://x.com/a"))

# A blocked page is never requested.
official._robots_text = lambda host: ("User-agent: *\nDisallow: /\n", True)
official.httpx.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetched a blocked page"))
d = official.fetch("https://example.com/careers/interviewing")
ok("blocked page not fetched", d["ok"] is False and "robots" in d["reason"])
official._robots_text = lambda host: ("", False)
ok("unreachable robots.txt means no fetch", official.fetch("https://example.org/x")["ok"] is False)

ok("registry finds Amazon by alias", official.employer("AWS")["name"] == "Amazon")
ok("registry finds P&G", official.employer("Procter & Gamble")["name"] == "Procter & Gamble")
ok("registry has no false match", official.employer("Acme Search Partners") is None)
ok("every registry page has an about line", all(p.get("about") for e in official.REGISTRY["employers"] for p in e["pages"]))

# O*NET matching on a tiny database
with onet.connect() as con:
    con.executemany("INSERT INTO occupations VALUES (?,?,?,?)", [
        ("15-1252.00", "Software Developers", "Develop software.", 4), ("13-2051.00", "Financial and Investment Analysts", "Analyze investments.", 4),
        ("19-1021.00", "Biochemists and Biophysicists", "Study chemistry of living things.", 5), ("25-1042.00", "Biological Science Teachers, Postsecondary", "Teach.", 5),
        ("11-2021.00", "Marketing Managers", "Plan marketing.", 4)])
    con.executemany("INSERT INTO titles VALUES (?,?)", [("15-1252.00", "Software Engineer"), ("13-2051.00", "Equity Research Analyst"),
                                                       ("25-1042.00", "Biology Professor"), ("11-2021.00", "Product Manager")])
    con.executemany("INSERT INTO tasks VALUES (?,?,?)", [("19-1021.00", "Isolate and analyze proteins.", 1)])
    con.execute("INSERT INTO meta VALUES ('version', '31.0')")
onet._title_index = None
ok("O*NET ready", onet.ready())
ok("software intern → Software Developers", onet.best_match("Software Engineering Intern")["code"] == "15-1252.00")
ok("private equity → Financial and Investment Analysts", onet.best_match("Private equity summer analyst")["code"] == "13-2051.00")
ok("protein sciences → Biochemists, not professors", onet.best_match("Research Intern, Protein Sciences")["code"] == "19-1021.00")
ok("product manager → Marketing Managers", onet.best_match("Product Manager Intern")["code"] == "11-2021.00")
ok("vague words match nothing", onet.best_match("remote unpaid tech internship") is None)
ok("occupation material", "Isolate and analyze proteins." in onet.material("19-1021.00"))

# Competency tags: a real quote keeps its level; an invented one is capped at 2
item = {"id": "s", "kind": "story", "situation": "Our club budget was cut", "action": "I led a vote and rebuilt the budget with the treasurer", "result": "We kept all events"}
g = grading._finish(item, {"scores": {}, "competencies": [
    {"name": "Leadership", "level": 4, "quote": "I led a vote"}, {"name": "teamwork", "level": 4, "quote": "invented words"},
    {"name": "Not a competency", "level": 5, "quote": ""}]})
comps = {c["name"]: c for c in g["competencies"]}
ok("competency kept with quote", comps["Leadership"]["level"] == 4 and comps["Leadership"]["level_name"] == "Advanced")
ok("competency without real quote capped", comps["Teamwork"]["level"] == 2 and not comps["Teamwork"]["quote"])
ok("unknown competency dropped", len(comps) == 2)
cov = grading.coverage({"s": {"grade": g}}, {"s": "Leadership story"})
ok("coverage", cov["Leadership"]["answers"] == ["Leadership story"] and not cov["Technology"]["answers"])
print("ALL PASSED")
