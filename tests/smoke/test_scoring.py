"""The shared scoring model (app/scoring.py): bands, the fit rubric computed from its parts, the work-authorization
gate, estimates without posting text, story and skills points on the same bands, and Home picking up the full fit."""
import os
import sys
import tempfile
from pathlib import Path

os.environ["JOB_AGENT_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[2]))

from app import automatch, scoring, store  # noqa: E402


def ok(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    assert cond, name


ok("band edges", [scoring.band(x)["label"] for x in (100, 80, 79, 65, 64, 50, 49, 0)] == ["Strong", "Strong", "Good", "Good", "Stretch", "Stretch", "Weak", "Weak"])
r = scoring.compute({"role": 4, "requirements": 2, "level": 4, "logistics": 4})
ok("weights: requirements count 3x", r["score"] == round(100 * (8 + 6 + 4 + 4) / 28) and not r["estimate"])
r = scoring.compute({"role": 4, "requirements": None, "level": 4, "logistics": 3}, has_posting=False)
ok("no posting text: requirements count as a middle 2, marked estimate", r["estimate"] and r["dims"]["requirements"]["score"] is None
   and r["dims"]["requirements"]["assumed"] == 2 and r["score"] == round(100 * (8 + 6 + 4 + 3) / 28))
r = scoring.compute({"role": 4, "requirements": 4, "level": 4, "logistics": 4}, gate="blocked")
ok("not eligible: capped at 40", r["score"] == 40 and r["capped"] and scoring.verdict(r) == "Not eligible as posted")
ok("unclear eligibility: flagged, not capped", scoring.compute({"role": 4, "requirements": 4, "level": 4, "logistics": 4}, gate="unclear")["score"] == 100)
ok("out-of-range and junk ratings handled", scoring.compute({"role": 9, "requirements": "x", "level": -2, "logistics": "3"})["dims"]["role"]["score"] == 4)
G = scoring.gate_from_work_authorization
ok("gate by rule for an international student", [G({"requirement": r, "ok_for_candidate": True}, True) for r in (
    "US citizens or permanent residents only", "security clearance", "no visa sponsorship now or in the future", "none stated", "CPT/OPT welcome")]
   == ["blocked", "blocked", "unclear", "ok", "ok"])
ok("'citizens only' needs the posting's words to say so", G({"requirement": "US citizens or permanent residents only",
   "evidence": "interns must be authorized to work in the US"}, True) == "unclear"
   and G({"requirement": "US citizens or permanent residents only", "evidence": "Must be a U.S. citizen"}, True) == "blocked")
ok("citizens never gated", G({"requirement": "US citizens or permanent residents only", "ok_for_candidate": False}, False) == "ok")
ok("gate from work authorization", [scoring.gate_from_work_authorization(w) for w in (
    {"ok_for_candidate": False}, {"ok_for_candidate": None, "requirement": "no visa sponsorship now or in the future"},
    {"ok_for_candidate": None, "requirement": "none stated"}, {"ok_for_candidate": True})] == ["blocked", "unclear", "ok", "ok"])
fit = scoring.finish_fit({"dims": {"role": 3, "requirements": 2, "level": 4, "logistics": 4},
                          "work_authorization": {"ok_for_candidate": None, "requirement": "no visa sponsorship now or in the future"}}, 5000)
ok("finish_fit: score, band, verdict, gate", fit["score"] == 71 and fit["band"]["label"] == "Good" and fit["verdict"] == "Good fit"
   and fit["gate"] == "unclear" and fit["scoring"] == "rubric-v1")
chk = lambda req, pref: [{"item": f"r{i}", "kind": "required", "met": m} for i, m in enumerate(req)] + [{"item": f"p{i}", "kind": "preferred", "met": m} for i, m in enumerate(pref)]
ok("checklist counting", [scoring.requirements_from_checklist(chk(r, p))[0] for r, p in (
    ([True] * 3, [True] * 6 + [False]), ([True] * 3, [True, True, False, False]), ([True] * 3, [True, False, False]),
    ([True, True, False], [True]), ([True, False, False], []), ([False, False], []), ([True], []))] == [4, 3, 2, 2, 1, 0, 4])
fit = scoring.finish_fit({"dims": {"role": 4, "requirements": None, "level": 4, "logistics": 4},
                          "requirements_checklist": chk([True, True], [True, False, False, False])}, 4000)
ok("fit uses the counted requirements", fit["dims"]["requirements"]["score"] == 2 and "2 of 2 required" in fit["dims"]["requirements"]["why"]
   and fit["score"] == round(100 * (8 + 6 + 4 + 4) / 28) and not fit["estimate"])
d = scoring.role_rule({"role": {"score": 3, "why": "x"}}, "Software Engineering", ["Software Engineering"])
ok("role rule: in-field job is 4", d["role"]["score"] == 4)
ok("role rule: out-of-field capped at 3", scoring.role_rule({"role": 4}, "Marketing", ["Software Engineering"])["role"]["score"] == 3)
ok("role rule: no targets, AI rating kept", scoring.role_rule({"role": 4}, "Marketing", [])["role"] == 4)
ok("stories: points on the same bands", scoring.band_from_points(18, 5, 20)["label"] == "Strong" and scoring.band_from_points(5, 5, 20)["score"] == 0)
ok("skills: points out of max", scoring.band_from_points(9, 0, 20)["label"] == "Weak")

store.save("matches", {"date": "2026-10-23", "items": [{"id": "p1", "job_id": "j1", "score": 80, "estimate": True, "date": "2026-10-23",
                                                        "status": "added", "title": "SWE", "company": "Amplify"}]})
automatch.sync_fit("j1", {"score": 58, "band": scoring.band(58)})
item = automatch.load()["items"][0]
ok("Home shows the full fit after the posting is read", item["fit_score"] == 58 and item["fit_band"]["label"] == "Stretch" and item["score"] == 80)
print("ALL PASSED")
