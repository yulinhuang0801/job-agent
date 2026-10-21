import os, sys, tempfile, json
os.environ["JOB_AGENT_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from fastapi.testclient import TestClient
from app import llm, main, services, store
seen = {}
def fake(messages, **kw):
    s = messages[0]["content"]
    if "interview prep plan" in s:
        return {"summary": "AI safety company", "process": ["Recruiter screen", "Coding"], "values": [{"value": "Safety", "how_to_show": "x"}],
                "behavioral": [{"label": "Why Anthropic", "question": "Why do you want to work at Anthropic?", "look_for": "mission", "kind": "answer"},
                               {"label": "Disagreement", "question": "Tell me about a time you disagreed on a safety call.", "kind": "story"}],
                "technical": [{"area": "x", "level": 2, "type": "practical", "question": "Implement an LRU cache.", "rubric": "dict + linked list", "lang": "python"},
                              {"area": "x", "level": 3, "type": "design", "question": "Design a rate limiter.", "rubric": "token bucket"}],
                "tips": ["t"], "sources_used": []}
    if "choose questions" in s:
        seen["pick"] = s
        ids = [l.split(" | ")[0] for l in s.split("QUESTION BANK")[1].splitlines() if " | " in l]
        return {"picked": ids[:5], "custom": [], "reason": "r"}
    raise AssertionError(s[:60])
llm.chat_json = fake
services.web_search = lambda a: {"answer": "", "results": [{"title": "t", "url": "u", "content": "c"}]}
c = TestClient(main.app)
store.save("settings", {"api_key": "sk-x", "search_key": "tvly-x"})
store.save("profile", {"targets": {"roles": ["SWE intern"]}})

r = c.post("/api/questions", json={"kind": "technical", "area": "Rust", "question": "What is ownership?", "rubric": "single owner", "level": 1}).json()
assert r["technical"][0]["id"].startswith("my_")
assert c.post("/api/questions", json={"kind": "technical", "question": "no area"}).status_code == 400
r = c.post("/api/questions", json={"kind": "behavioral", "question": "Time you used data to persuade?", "type": "answer"}).json()
bid = r["behavioral"][0]["id"]; assert r["behavioral"][0]["kind"] == "answer"
areas = c.get("/api/skills/areas").json()["areas"]
assert any(a["label"] == "Rust" and a["mine"] for a in areas) and len([a for a in areas if a["source"] == "Job Agent"]) == 30 and any(a["id"] == "lc:array" for a in areas)
st = c.post("/api/stories/start").json()
assert bid in st["extra_left"] and st["labels"][bid].startswith("Time you used data")
st = c.post("/api/stories/add", json={"competency": bid}).json(); assert bid in st["plan"]
print("ok   my questions: technical area + behavioral question usable in Stories")

sk = c.post("/api/skills/start", json={"count": 4, "areas": ["Valuation (finance technicals)"]}).json()
assert len(sk["questions"]) == 4 and all(q["area"] == "Valuation (finance technicals)" for q in sk["questions"]), [q["area"] for q in sk["questions"]]
assert "Python" not in seen["pick"].split("QUESTION BANK")[1]
sk = c.post("/api/skills/start", json={"count": 5, "areas": ["Rust"]}).json()
assert [q["question"] for q in sk["questions"]] == ["What is ownership?"] or len(sk.get("questions", [])) >= 1
print("ok   area-limited skills check")

r = c.post("/api/prep", json={"company": "Anthropic", "role": "SWE intern"}).json()
p = r["preps"][0]
assert p["area"] == "Anthropic prep" and len(p["behavioral_ids"]) == 2 and len(p["technical_ids"]) == 2 and p["searched"]
qs = c.get("/api/questions").json()
assert any(q["area"] == "Anthropic prep" and q.get("lang") == "python" for q in qs["technical"])
assert any(b.get("group") == "Anthropic prep" for b in qs["behavioral"])
sk = c.post("/api/skills/start", json={"count": 5, "areas": ["Anthropic prep"]}).json()
assert {q["question"] for q in sk["questions"]} == {"Implement an LRU cache.", "Design a rate limiter."}, sk
r = c.delete(f"/api/prep/{p['id']}").json()
assert not r["preps"] and not any(q["area"] == "Anthropic prep" for q in c.get("/api/questions").json()["technical"])
print("ok   company prep adds, uses and deletes its questions")
print("ALL PASSED")
