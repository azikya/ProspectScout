"""SQLite persistence: research is stored once per company (domain), messages per contact.

Contact columns use ProspectIQ's ``ProspectField`` names so data can move into
ProspectIQ later. Columns ending in ``_json`` are encoded/decoded transparently.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

from .. import config

_LOCK = threading.Lock()

CONTACT_FIELDS = (
    "first_name", "last_name", "full_name", "job_title", "seniority", "department", "linkedin_url",
    "email", "email_status", "phone", "company_name", "company_domain", "website", "company_linkedin",
    "industry", "country", "city", "employee_count", "funding", "latest_funding", "latest_funding_date",
    "technologies", "company_description", "notes",
)
COMPANY_STATUSES = ("queued", "crawling", "analyzing", "done", "needs_review", "skipped", "unreachable", "failed")
OUTREACH_STATUSES = ("new", "contacted", "replied", "call_booked", "won", "lost", "skip")

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY, source TEXT, total INTEGER, created_at INTEGER);
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY, domain TEXT UNIQUE NOT NULL, website TEXT, company_name TEXT,
    status TEXT NOT NULL DEFAULT 'queued', stage TEXT, error TEXT,
    crawl_json TEXT, facts_json TEXT, analysis_json TEXT, score INTEGER, fit TEXT,
    tz TEXT, tz_source TEXT, industry TEXT, country TEXT, created_at INTEGER, updated_at INTEGER);
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY, company_id INTEGER REFERENCES companies(id), batch_id INTEGER,
    {", ".join(f + " TEXT" for f in CONTACT_FIELDS)},
    status TEXT NOT NULL DEFAULT 'ready', messages_json TEXT, messages_status TEXT DEFAULT 'pending',
    outreach_status TEXT DEFAULT 'new', contacted_at INTEGER, manual_signals_json TEXT,
    created_at INTEGER, updated_at INTEGER);
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company_id);
CREATE INDEX IF NOT EXISTS idx_companies_status ON companies(status);
"""


def default_path() -> str:
    return os.path.join(config.home_dir(), "prospectscout.db")


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or default_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _now() -> int:
    return int(time.time())


def _encode(fields: dict) -> dict:
    return {k: (json.dumps(v) if k.endswith("_json") and v is not None and not isinstance(v, str) else v)
            for k, v in fields.items()}


def _decode(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    out = dict(row)
    for k, v in out.items():
        if k.endswith("_json") and isinstance(v, str):
            try:
                out[k] = json.loads(v)
            except ValueError:
                pass
    return out


def create_batch(conn: sqlite3.Connection, source: str, total: int) -> int:
    with _LOCK, conn:
        cur = conn.execute("INSERT INTO batches(source, total, created_at) VALUES (?,?,?)",
                           (source, total, _now()))
        return int(cur.lastrowid)


def _find_duplicate(conn: sqlite3.Connection, fields: dict) -> bool:
    email = (fields.get("email") or "").strip().lower()
    if email:
        row = conn.execute("SELECT 1 FROM contacts WHERE lower(email)=? LIMIT 1", (email,)).fetchone()
        return row is not None
    name = (fields.get("full_name") or "").strip().lower()
    domain = (fields.get("company_domain") or "").strip().lower()
    if domain:  # same person (or the same nameless website-only row) at the same company
        row = conn.execute("SELECT 1 FROM contacts WHERE lower(COALESCE(full_name,''))=? AND lower(company_domain)=? "
                           "LIMIT 1", (name, domain)).fetchone()
        return row is not None
    if name:  # no email, no website: match on phone or LinkedIn
        for col in ("phone", "linkedin_url"):
            val = (fields.get(col) or "").strip().lower()
            if val and conn.execute(f"SELECT 1 FROM contacts WHERE lower(COALESCE(full_name,''))=? AND lower({col})=? "
                                    "LIMIT 1", (name, val)).fetchone():
                return True
    return False


def add_contact(conn: sqlite3.Connection, fields: dict, batch_id: int | None = None) -> dict:
    fields = {k: v for k, v in (fields or {}).items() if k in CONTACT_FIELDS}
    domain = (fields.get("company_domain") or "").strip().lower() or None
    if domain:
        fields["company_domain"] = domain
    result = {"contact_id": None, "company_id": None, "company_new": False,
              "duplicate": False, "needs_website": domain is None}
    with _LOCK, conn:
        if _find_duplicate(conn, fields):
            result["duplicate"] = True
            result["needs_website"] = False
            return result
        now = _now()
        if domain:
            row = conn.execute("SELECT id FROM companies WHERE domain=?", (domain,)).fetchone()
            if row:
                result["company_id"] = row["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO companies(domain, website, company_name, status, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (domain, fields.get("website") or f"https://{domain}", fields.get("company_name"),
                     "queued", now, now))
                result["company_id"] = int(cur.lastrowid)
                result["company_new"] = True
        cols = list(fields) + ["company_id", "batch_id", "status", "messages_status", "outreach_status",
                               "created_at", "updated_at"]
        vals = list(fields.values()) + [result["company_id"], batch_id,
                                        "ready" if domain else "needs_website", "pending", "new", now, now]
        cur = conn.execute(f"INSERT INTO contacts({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
        result["contact_id"] = int(cur.lastrowid)
    return result


def set_contact_website(conn: sqlite3.Connection, contact_id: int, website: str, domain: str) -> dict:
    """Give a 'needs website' contact a company to research (creates the company if new)."""
    domain = domain.strip().lower()
    now = _now()
    with _LOCK, conn:
        row = conn.execute("SELECT id FROM companies WHERE domain=?", (domain,)).fetchone()
        company_new = row is None
        if row:
            company_id = row["id"]
        else:
            cur = conn.execute("INSERT INTO companies(domain, website, status, created_at, updated_at) VALUES (?,?,?,?,?)",
                               (domain, website, "queued", now, now))
            company_id = int(cur.lastrowid)
        conn.execute("UPDATE contacts SET company_id=?, company_domain=?, website=?, status='ready', "
                     "messages_status='pending', updated_at=? WHERE id=?", (company_id, domain, website, now, contact_id))
    return {"company_id": company_id, "company_new": company_new}


def next_queued_company(conn: sqlite3.Connection) -> dict | None:
    with _LOCK, conn:
        row = conn.execute(
            "UPDATE companies SET status='crawling', stage='Starting…', updated_at=? "
            "WHERE id=(SELECT id FROM companies WHERE status='queued' ORDER BY id LIMIT 1) RETURNING *",
            (_now(),)).fetchone()
    return _decode(row)


def next_pending_contact(conn: sqlite3.Connection) -> dict | None:
    """A contact whose company is researched but who has no messages yet (claims it as 'writing')."""
    with _LOCK, conn:
        row = conn.execute(
            "UPDATE contacts SET messages_status='writing', updated_at=? WHERE id=("
            "SELECT c.id FROM contacts c JOIN companies co ON co.id=c.company_id "
            "WHERE c.messages_status='pending' AND c.status='ready' AND co.status IN ('done','needs_review') "
            "ORDER BY c.id LIMIT 1) RETURNING *", (_now(),)).fetchone()
    return _decode(row)


def _update(conn: sqlite3.Connection, table: str, row_id: int, fields: dict) -> None:
    if not fields:
        return
    fields = _encode(dict(fields, updated_at=_now()))
    sets = ", ".join(f"{k}=?" for k in fields)
    with _LOCK, conn:
        conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", [*fields.values(), row_id])


def set_company(conn: sqlite3.Connection, company_id: int, **fields) -> None:
    _update(conn, "companies", company_id, fields)


def get_company(conn: sqlite3.Connection, company_id: int) -> dict | None:
    return _decode(conn.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone())


def set_contact(conn: sqlite3.Connection, contact_id: int, **fields) -> None:
    if fields.get("outreach_status") == "contacted":
        current = conn.execute("SELECT contacted_at FROM contacts WHERE id=?", (contact_id,)).fetchone()
        if current is not None and not current["contacted_at"]:
            fields["contacted_at"] = _now()
    _update(conn, "contacts", contact_id, fields)


def get_contact(conn: sqlite3.Connection, contact_id: int) -> dict | None:
    return _decode(conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone())


def contacts_for_company(conn: sqlite3.Connection, company_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM contacts WHERE company_id=? ORDER BY id", (company_id,)).fetchall()
    return [_decode(r) for r in rows]


def list_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT c.id AS contact_id, c.company_id, c.full_name, c.job_title, c.email, c.phone, c.linkedin_url, "
        "c.company_domain, c.status AS contact_status, c.messages_status, c.outreach_status, c.contacted_at, "
        "co.domain, co.website, COALESCE(co.company_name, c.company_name) AS company_name, "
        "co.status AS company_status, co.stage, co.error, co.score, co.fit, co.tz "
        "FROM contacts c LEFT JOIN companies co ON co.id = c.company_id "
        "ORDER BY COALESCE(co.score, -1) DESC, c.id DESC").fetchall()
    return [dict(r) for r in rows]


def all_companies(conn: sqlite3.Connection) -> list[dict]:
    return [_decode(r) for r in conn.execute("SELECT * FROM companies ORDER BY id").fetchall()]


def all_contacts(conn: sqlite3.Connection) -> list[dict]:
    return [_decode(r) for r in conn.execute("SELECT * FROM contacts ORDER BY id").fetchall()]


def counts(conn: sqlite3.Connection) -> dict:
    by = dict(conn.execute("SELECT status, COUNT(*) FROM companies GROUP BY status").fetchall())
    contacts = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    needs = conn.execute("SELECT COUNT(*) FROM contacts WHERE status='needs_website'").fetchone()[0]
    return {
        "total": sum(by.values()),
        "queued": by.get("queued", 0),
        "in_progress": by.get("crawling", 0) + by.get("analyzing", 0),
        "done": sum(by.get(s, 0) for s in ("done", "needs_review", "skipped", "unreachable")),
        "failed": by.get("failed", 0),
        "contacts": contacts,
        "needs_website": needs,
    }


def reset_in_progress(conn: sqlite3.Connection) -> int:
    with _LOCK, conn:
        cur = conn.execute("UPDATE companies SET status='queued', stage=NULL WHERE status IN ('crawling','analyzing')")
        cur2 = conn.execute("UPDATE contacts SET messages_status='pending' WHERE messages_status='writing'")
        return cur.rowcount + cur2.rowcount


def delete_contact(conn: sqlite3.Connection, contact_id: int) -> None:
    """Remove a contact; drop its company too when no contacts remain."""
    with _LOCK, conn:
        row = conn.execute("SELECT company_id FROM contacts WHERE id=?", (contact_id,)).fetchone()
        conn.execute("DELETE FROM contacts WHERE id=?", (contact_id,))
        if row and row["company_id"]:
            left = conn.execute("SELECT COUNT(*) FROM contacts WHERE company_id=?", (row["company_id"],)).fetchone()[0]
            if not left:
                conn.execute("DELETE FROM companies WHERE id=?", (row["company_id"],))


def requeue_company(conn: sqlite3.Connection, company_id: int) -> None:
    set_company(conn, company_id, status="queued", stage=None, error=None)
    with _LOCK, conn:  # prospects already contacted keep the messages that were actually sent
        conn.execute("UPDATE contacts SET messages_status='pending' WHERE company_id=? "
                     "AND COALESCE(outreach_status, 'new')='new'", (company_id,))
