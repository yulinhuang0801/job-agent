"""Grading regression test: grade a fixed set of answers and compare with the expected scores.

Run it after changing a grading prompt, the rubric or the model:

    .venv/bin/python tests/calibration/run_calibration.py            # stories and skills
    .venv/bin/python tests/calibration/run_calibration.py --only stories --repeat 2

It uses the AI keys saved in Settings (it costs a few cents) and never writes to your stories or
skills. It fails (exit code 1) when fewer than 85% of scores land in their expected range, or any
overall grade is off by more than one. With --repeat, it also reports how often a second run gives
a different score (consistency). Results are saved to data/calibration_last.json."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app import assess, coderun, grading, store  # noqa: E402

HERE = Path(__file__).resolve().parent


def within(v, rng):
    return rng[0] <= v <= rng[1]


def off_by(v, rng):
    return 0 if within(v, rng) else min(abs(v - rng[0]), abs(v - rng[1]))


def run_stories(repeat):
    items = json.loads((HERE / "stories.json").read_text(encoding="utf-8"))["items"]
    runs = [grading.grade(items) for _ in range(repeat)]
    rows, checks, hits, big_misses, unstable = [], 0, 0, 0, 0
    for it in items:
        g = runs[0][it["id"]]
        scores = {d["key"]: d["score"] for d in g["dims"]}
        line = []
        for key, rng in it["expect"].items():
            got = g["stars"] if key == "stars" else scores.get(key)
            checks += 1
            ok = within(got, rng)
            hits += ok
            if key == "stars" and off_by(got, rng) > 1:
                big_misses += 1
            line.append(f"{key}={got}{'' if ok else '✗' + str(rng)}")
        for other in runs[1:]:
            o = other[it["id"]]
            if o["stars"] != g["stars"] or any(a["score"] != b["score"] for a, b in zip(g["dims"], o["dims"])):
                unstable += 1
                break
        rows.append({"id": it["id"], "stars": g["stars"], "scores": scores, "top_fix": g["top_fix"],
                     "unverified_quotes": sum(1 for d in g["dims"] if "Capped at 2" in d["why"])})
        print(f"  {it['id']:24s} " + "  ".join(line))
    return {"checks": checks, "hits": hits, "big_misses": big_misses, "unstable": unstable, "rows": rows, "n": len(items)}


def run_skills(repeat):
    items = json.loads((HERE / "skills.json").read_text(encoding="utf-8"))["items"]
    questions = [{"id": i["id"], "area": i["area"], "question": i["question"], "rubric": i["rubric"],
                  "type": i.get("type", "concept"), "link": f"https://leetcode.com/problems/{i['slug']}/" if i.get("slug") else ""}
                 for i in items]
    answers = {i["id"]: i["answer"] for i in items}
    tests = {}
    if any(i.get("slug") for i in items):
        coderun.ensure(wait=True, timeout=300)
        tests = {i["id"]: coderun.run(i["slug"], i["answer"]) for i in items if i.get("slug")}
    runs = [assess.score_items(questions, answers, tests)[0] for _ in range(repeat)]
    checks, hits, big_misses, unstable, rows = 0, 0, 0, 0, []
    for n, it in enumerate(items):
        got = runs[0][n]["score"]
        ok = within(got, it["expect"])
        checks += 1
        hits += ok
        big_misses += off_by(got, it["expect"]) > 1
        unstable += any(r[n]["score"] != got for r in runs[1:])
        t = tests.get(it["id"])
        print(f"  {it['id']:24s} score={got}{'' if ok else '✗' + str(it['expect'])}"
              + (f"  tests {t['passed']}/{t['total']}" if t and t.get("ran") else ""))
        rows.append({"id": it["id"], "score": got, "feedback": runs[0][n]["feedback"]})
    return {"checks": checks, "hits": hits, "big_misses": big_misses, "unstable": unstable, "rows": rows, "n": len(items)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["stories", "skills"])
    ap.add_argument("--repeat", type=int, default=1, help="grade each answer this many times to measure consistency")
    args = ap.parse_args()
    report, failed = {"rubric": grading.RUBRIC_VERSION, "model": store.get_settings().get("model")}, False
    for name, fn in (("stories", run_stories), ("skills", run_skills)):
        if args.only and args.only != name:
            continue
        print(f"\n{name}:")
        r = fn(max(1, args.repeat))
        rate = r["hits"] / max(1, r["checks"])
        print(f"  → {r['hits']}/{r['checks']} scores in range ({rate:.0%}), {r['big_misses']} overall grades off by more than one"
              + (f", {r['unstable']}/{r['n']} answers scored differently on a second run" if args.repeat > 1 else ""))
        report[name] = r
        failed |= rate < 0.85 or r["big_misses"] > 0
    (store.DATA_DIR / "calibration_last.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nFAILED" if failed else "\nPASSED")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
