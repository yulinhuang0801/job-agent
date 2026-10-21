"""Local firm and job database (SQLite in data/). Filled by app/crawlers.py, searched by the Find tab."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from . import store, taxonomy

PATH = store.DATA_DIR / "jobs_db.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS firms (
  id TEXT PRIMARY KEY, source TEXT, name TEXT, legal_name TEXT, kind TEXT, industry TEXT, category TEXT,
  city TEXT, state TEXT, country TEXT, website TEXT, domain TEXT, phone TEXT,
  employees INTEGER, size TEXT, assets INTEGER, small INTEGER, fund_types TEXT,
  filed TEXT, founded TEXT, raised INTEGER, extra TEXT
);
CREATE INDEX IF NOT EXISTS firms_industry ON firms(industry);
CREATE INDEX IF NOT EXISTS firms_state ON firms(state);
CREATE INDEX IF NOT EXISTS firms_source ON firms(source);
CREATE TABLE IF NOT EXISTS people (firm_id TEXT, name TEXT, title TEXT, source TEXT);
CREATE INDEX IF NOT EXISTS people_firm ON people(firm_id);
CREATE TABLE IF NOT EXISTS postings (
  id TEXT PRIMARY KEY, source TEXT, board TEXT, company TEXT, domain TEXT, title TEXT, department TEXT,
  function TEXT, job_type TEXT, term TEXT, location TEXT, state TEXT, country TEXT, remote INTEGER,
  industry TEXT, url TEXT, posted TEXT, sponsorship TEXT, description TEXT, seen TEXT
);
CREATE INDEX IF NOT EXISTS postings_type ON postings(job_type);
CREATE INDEX IF NOT EXISTS postings_function ON postings(function);
CREATE INDEX IF NOT EXISTS postings_company ON postings(company);
CREATE TABLE IF NOT EXISTS companies (board TEXT, token TEXT, name TEXT, industry TEXT, website TEXT, jobs INTEGER,
  status TEXT, checked TEXT, PRIMARY KEY (board, token));
CREATE TABLE IF NOT EXISTS sources (name TEXT PRIMARY KEY, status TEXT, message TEXT, rows INTEGER, updated TEXT);
"""


@contextmanager
def connect():
    store.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def set_source(name, status, message="", rows=None):
    with connect() as con:
        old = con.execute("SELECT rows FROM sources WHERE name=?", (name,)).fetchone()
        con.execute("INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?)",
                    (name, status, message, rows if rows is not None else (old["rows"] if old else 0),
                     datetime.now().isoformat(timespec="seconds")))


def sources():
    with connect() as con:
        return {r["name"]: dict(r) for r in con.execute("SELECT * FROM sources")}


def upsert(con, table, rows):
    if not rows:
        return
    cols = list(rows[0].keys())
    con.executemany(f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [tuple(json.dumps(r[c]) if isinstance(r[c], (dict, list)) else r[c] for c in cols) for r in rows])


def _where(filters, text_cols):
    """Build WHERE from {column: value or [values]}; 'q' searches text columns."""
    clauses, args = [], []
    for col, val in filters.items():
        if val in (None, "", []):
            continue
        if col == "q":
            words = [w for w in str(val).split() if w][:6]
            for w in words:
                clauses.append("(" + " OR ".join(f"{c} LIKE ?" for c in text_cols) + ")")
                args += [f"%{w}%"] * len(text_cols)
        elif col == "min_employees":
            clauses.append("employees >= ?"); args.append(int(val))
        elif col == "max_employees":
            clauses.append("employees <= ?"); args.append(int(val))
        elif col == "since":
            clauses.append("filed >= ?"); args.append(val)
        elif col == "hide_citizen":
            clauses.append("COALESCE(sponsorship, '') != 'US citizens only'")
        elif col == "hide_nosponsor":
            clauses.append("COALESCE(sponsorship, '') != 'No sponsorship'")
        elif col == "has_website":
            clauses.append("website != ''")
        elif col == "has_people":
            clauses.append("EXISTS (SELECT 1 FROM people p WHERE p.firm_id = firms.id)")
        elif isinstance(val, list):
            clauses.append(f"{col} IN ({','.join('?' * len(val))})"); args += val
        else:
            clauses.append(f"{col} = ?"); args.append(val)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", args


FACETS = {"firms": ["industry", "category", "state", "size", "source"],
          "postings": ["job_type", "function", "industry", "term", "state", "country", "sponsorship", "source"]}
TEXT = {"firms": ["name", "legal_name", "city"], "postings": ["title", "company", "location", "department"]}
ORDER = {"firms": "filed DESC", "postings": "COALESCE(posted, '') DESC"}


def search(table, filters, page=1, per_page=40):
    if table not in FACETS:
        raise ValueError(table)
    where, args = _where(filters, TEXT[table])
    with connect() as con:
        total = con.execute(f"SELECT COUNT(*) FROM {table}{where}", args).fetchone()[0]
        cols = "*" if table == "firms" else "id, source, board, company, domain, title, department, function, job_type, term, location, state, country, remote, industry, url, posted, sponsorship"
        rows = [dict(r) for r in con.execute(f"SELECT {cols} FROM {table}{where} ORDER BY {ORDER[table]} LIMIT ? OFFSET ?",
                                             args + [per_page, (max(1, page) - 1) * per_page])]
        if table == "firms" and rows:
            ids = [r["id"] for r in rows]
            people = {}
            for p in con.execute(f"SELECT * FROM people WHERE firm_id IN ({','.join('?' * len(ids))})", ids):
                people.setdefault(p["firm_id"], []).append({"name": p["name"], "title": p["title"]})
            for r in rows:
                r["people"] = people.get(r["id"], [])[:6]
                r["extra"] = json.loads(r["extra"] or "{}")
        facets = {}
        for col in FACETS[table]:
            facets[col] = [{"value": v, "count": n} for v, n in con.execute(
                f"SELECT {col}, COUNT(*) n FROM {table}{where} GROUP BY {col} ORDER BY n DESC LIMIT 60", args) if v]
        if table == "postings":
            facets["remote"] = [{"value": "1", "count": con.execute(f"SELECT COUNT(*) FROM postings{where}{' AND' if where else ' WHERE'} remote = 1", args).fetchone()[0]}]
    return {"total": total, "page": page, "per_page": per_page, "rows": rows, "facets": facets}


def counts():
    with connect() as con:
        return {"firms": con.execute("SELECT COUNT(*) FROM firms").fetchone()[0],
                "people": con.execute("SELECT COUNT(*) FROM people").fetchone()[0],
                "postings": con.execute("SELECT COUNT(*) FROM postings").fetchone()[0],
                "boards": con.execute("SELECT COUNT(*) FROM companies").fetchone()[0]}


def vocab():
    return {"industries": taxonomy.INDUSTRIES, "functions": taxonomy.FUNCTIONS, "job_types": taxonomy.JOB_TYPES,
            "sizes": [s[0] for s in taxonomy.SIZES], "states": taxonomy.US_STATES}
