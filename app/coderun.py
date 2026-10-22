"""Run Python answers to LeetCode problems against real test cases.

Test cases come from newfacade/LeetCodeDataset on Hugging Face (Apache-2.0): about 2,870 problems,
each with a check() of many asserts and the entry point (e.g. Solution().twoSum). It's downloaded
once (about 100 MB, trimmed to the fields we need) into data/qlib/.

The answer runs in a separate Python process with a time limit per test and CPU, memory and file
limits. That is enough for a single user running their own code on their own computer; a hosted,
multi-user version needs a real sandbox (a container or a service like Judge0)."""
import ast
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

import httpx

from . import store

DATASET = "https://huggingface.co/datasets/newfacade/LeetCodeDataset/resolve/main/"
FILES = ("LeetCodeDataset-train.jsonl", "LeetCodeDataset-test.jsonl")
INDEX = store.DATA_DIR / "qlib" / "lcd"  # one small file per problem, plus _ready.json
MAX_TIMEOUTS = 3
PER_TEST_SECONDS = 2
TOTAL_SECONDS = 20

_lock = threading.Lock()
_state = {"downloading": False, "error": ""}
_index = None


class RunError(Exception):
    pass


def _download():
    rows = {}
    for name in FILES:
        with httpx.stream("GET", DATASET + name, timeout=httpx.Timeout(30, read=120), follow_redirects=True) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("task_id") and d.get("test") and d.get("entry_point"):
                    rows[d["task_id"]] = {"entry_point": d["entry_point"], "test": d["test"], "preamble": d.get("prompt", "")}
    INDEX.mkdir(parents=True, exist_ok=True)
    for slug, spec in rows.items():
        (INDEX / f"{slug}.json").write_text(json.dumps(spec), encoding="utf-8")
    (INDEX / "_ready.json").write_text(json.dumps({"problems": len(rows), "at": time.time()}), encoding="utf-8")
    return {"problems": len(rows)}


def _load():
    """Small manifest (problem count) once the download finished, else None."""
    global _index
    if _index is None and (INDEX / "_ready.json").exists():
        try:
            _index = json.loads((INDEX / "_ready.json").read_text(encoding="utf-8"))
        except ValueError:
            _index = None
    return _index


def _spec(slug):
    p = INDEX / f"{slug}.json"
    if not slug or "/" in slug or not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def ensure(wait=False, timeout=180):
    """Make sure the test cases are on disk. Without wait, starts a background download."""
    global _index
    if _load() is not None:
        return True

    def work():
        global _index
        try:
            _index = _download()
            _state["error"] = ""
        except (httpx.HTTPError, OSError) as e:
            _state["error"] = f"Couldn't download the LeetCode test cases ({e.__class__.__name__})."
        finally:
            _state["downloading"] = False

    with _lock:
        if not _state["downloading"] and _index is None:
            _state["downloading"] = True
            threading.Thread(target=work, daemon=True).start()
    if not wait:
        return False
    end = time.time() + timeout
    while _state["downloading"] and time.time() < end:
        time.sleep(0.5)
    return _load() is not None


def status():
    return {"ready": _load() is not None, "downloading": _state["downloading"], "error": _state["error"],
            "problems": (_index or {}).get("problems", 0)}


def has_tests(slug):
    return _load() is not None and _spec(slug) is not None


def slug_for(question):
    """The LeetCode slug of a Skills-check question, from its id (lc_<slug>) or its link."""
    if str(question.get("id", "")).startswith("lc_"):
        return question["id"][3:]
    link = question.get("link") or ""
    if "leetcode.com/problems/" in link:
        return link.split("leetcode.com/problems/")[1].split("/")[0]
    return ""


def looks_like_python(code):
    try:
        ast.parse(code)
        return True
    except SyntaxError:
        # Braces and semicolons mean another language; otherwise it's Python with a mistake.
        return not (code.count("{") >= 2 or code.count(";") >= 3 or "public " in code or "#include" in code)


# The harness runs inside the child process. It reads the job from stdin, keeps the answer's own
# prints away from the results, and reports one line per test so a timeout keeps earlier results.
HARNESS = r'''
import ast, io, json, signal, sys, resource
job = json.loads(sys.stdin.read())
out = sys.stdout
def report(**kw):
    out.write("@@R " + json.dumps(kw) + "\n"); out.flush()
for lim, val in ((resource.RLIMIT_CPU, (job["cpu"], job["cpu"] + 1)), (resource.RLIMIT_FSIZE, (1 << 20, 1 << 20))):
    try: resource.setrlimit(lim, val)
    except (ValueError, OSError): pass
try: resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
except (ValueError, OSError): pass
class Timeout(Exception): pass
def on_alarm(*a): raise Timeout()
signal.signal(signal.SIGALRM, on_alarm)
sys.stdout = io.StringIO()
pre = job["preamble"].replace("from sortedcontainers import SortedList", "try:\n    from sortedcontainers import SortedList\nexcept ImportError:\n    SortedList = None")
ns = {"__name__": "solution"}
try:
    exec(compile(pre, "setup", "exec"), ns)
except Exception as e:
    pass
try:
    exec(compile(job["code"], "your_code.py", "exec"), ns)
    candidate = eval(job["entry_point"], ns)
except Exception as e:
    report(fatal=f"{e.__class__.__name__}: {e}"[:400]); sys.exit(0)
src = job["test"]
fn = next((n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)), None)
env = dict(ns); env["candidate"] = candidate
def ev(node):
    return eval(compile(ast.Expression(node), "test", "eval"), env)
def unordered(a, b):
    if not (isinstance(a, list) and isinstance(b, list)) or len(a) != len(b):
        return False
    norm = lambda x: sorted(repr(sorted(i) if isinstance(i, list) and job["any_order"] == "deep" else i) for i in x)
    return norm(a) == norm(b)
timeouts = 0
def short(v):
    s = repr(v)
    return s if len(s) <= 200 else s[:200] + "…"
for st in (fn.body if fn else []):
    if not isinstance(st, ast.Assert):
        continue
    case = (ast.get_source_segment(src, st) or "")[7:]
    signal.alarm(job["per_test"])
    try:
        t = st.test
        if isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], ast.Eq):
            got, exp = ev(t.left), ev(t.comparators[0])
            ok = got == exp
            if not ok and job["any_order"] and unordered(got, exp):
                report(ok=True, case=case[:300], note="same answer in a different order")
            else:
                report(ok=ok, case=case[:300], got=None if ok else short(got), expected=None if ok else short(exp))
        else:
            report(ok=bool(ev(t)), case=case[:300])
    except Timeout:
        report(ok=False, case=case[:300], error="Took longer than %ds (too slow, or an infinite loop)" % job["per_test"])
        timeouts += 1
        if timeouts >= job["max_timeouts"]:
            report(stopped=True); break
    except Exception as e:
        report(ok=False, case=case[:300], error=f"{e.__class__.__name__}: {e}"[:300])
    finally:
        signal.alarm(0)
report(done=True)
'''


def _any_order(slug):
    """"deep" when the problem says lists and their items may come in any order, True for the outer list only."""
    from . import qlibrary
    try:
        text = qlibrary.problem(slug)["text"].lower()
    except Exception:
        return False
    if "any order" not in text:
        return False
    return "deep" if any(w in text for w in ("combinations", "permutations", "subsets", "triplets", "quadruplets", "groups")) else True


def run(slug, code, max_tests=40):
    """Run the answer against the problem's tests. Returns a summary for the page and the grader."""
    if not code.strip():
        return {"ran": False, "reason": "No code yet."}
    if not looks_like_python(code):
        return {"ran": False, "reason": "Tests run only for Python answers. Other languages are graded by reading the code."}
    if os.name == "nt":  # the test harness needs Unix time and memory limits
        return {"ran": False, "reason": "Running tests needs macOS or Linux; on Windows your code is graded by reading it."}
    if _load() is None:
        ensure()
        return {"ran": False, "reason": "Downloading the LeetCode test cases (about 100 MB, once). Try again in a minute.", "downloading": True}
    spec = _spec(slug)
    if not spec:
        return {"ran": False, "reason": "No test cases for this problem in the dataset; it's graded by reading the code."}
    try:
        ast.parse(code)
    except SyntaxError as e:
        return {"ran": True, "passed": 0, "total": 0, "fatal": f"SyntaxError on line {e.lineno}: {e.msg}", "failures": []}
    job = {"code": code, "preamble": spec["preamble"], "entry_point": spec["entry_point"], "test": spec["test"],
           "per_test": PER_TEST_SECONDS, "cpu": TOTAL_SECONDS, "max_timeouts": MAX_TIMEOUTS, "any_order": _any_order(slug)}
    results, fatal, start = [], "", time.time()
    with tempfile.TemporaryDirectory() as tmp:
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8", "HOME": tmp}
        try:
            proc = subprocess.run([sys.executable, "-I", "-c", HARNESS], input=json.dumps(job), capture_output=True,
                                  text=True, timeout=TOTAL_SECONDS + 5, cwd=tmp, env=env)
            stdout, stderr = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as e:
            stdout = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            stderr = ""
            fatal = f"Stopped after {TOTAL_SECONDS} seconds."
    for line in stdout.splitlines():
        if line.startswith("@@R "):
            try:
                r = json.loads(line[4:])
            except ValueError:
                continue
            if r.get("fatal"):
                fatal = r["fatal"]
            elif r.get("stopped"):
                fatal = f"Stopped after {MAX_TIMEOUTS} test cases timed out (too slow, or an infinite loop)."
            elif "ok" in r:
                results.append(r)
    if not results and not fatal and stderr.strip():
        fatal = stderr.strip().splitlines()[-1][:300]
    results = results[:max_tests] if len(results) > max_tests else results
    failures = [r for r in results if not r["ok"]]
    return {"ran": True, "passed": len(results) - len(failures), "total": len(results), "fatal": fatal,
            "failures": failures[:5], "reordered": sum(1 for r in results if r.get("note")), "timeouts": sum(1 for r in failures if "longer than" in (r.get("error") or "")),
            "seconds": round(time.time() - start, 1), "source": "LeetCodeDataset (newfacade, Apache-2.0)"}


def summary_for_grader(res):
    if not res or not res.get("ran"):
        return (res or {}).get("reason", "")
    if res.get("fatal") and not res.get("total"):
        return f"The code did not run: {res['fatal']}"
    text = f"Passed {res['passed']} of {res['total']} test cases."
    for f in res.get("failures") or []:
        text += f"\n- Failed: {f['case']}" + (f" → got {f['got']}, expected {f['expected']}" if f.get("got") is not None else "") \
                + (f" ({f['error']})" if f.get("error") else "")
    return text
